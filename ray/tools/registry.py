"""Tool registry: one validated, permission-gated, audited execution path.

Every capability RAY has - read a file, open Chrome, create a task, speak - is a
Tool.  The orchestrator and the 12 agents can only act through this registry, so
there is exactly one place where arguments are validated, permissions are
enforced and actions are logged.
"""

from __future__ import annotations

import inspect
import json
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.events import EventBus, bus as default_bus
from ..core.logging_setup import get_logger
from ..security.audit import AuditLog
from ..security.permissions import LEVEL_AUTO, PermissionManager

log = get_logger("tools.registry")


class ToolArgumentError(ValueError):
    pass


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str = ""
    tool: str = ""
    level: int = LEVEL_AUTO
    verified: bool | None = None
    requires_approval: int | None = None
    duration_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tool": self.tool,
            "data": self.data,
            "error": self.error,
            "level": self.level,
            "verified": self.verified,
            "requires_approval": self.requires_approval,
            "duration_ms": self.duration_ms,
        }


@dataclass
class Tool:
    name: str
    description: str
    handler: Callable[..., Any]
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    category: str = "general"
    level: int = LEVEL_AUTO
    destructive: bool = False
    verify: Callable[[Any], tuple[bool, str]] | None = None
    examples: list[str] = field(default_factory=list)

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "level": self.level,
            "destructive": self.destructive,
            "parameters": self.parameters,
            "examples": self.examples,
        }

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def validate_arguments(schema: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """Small, strict JSON-Schema subset validator.

    Supports object/string/number/integer/boolean/array types, required fields,
    enums and nested objects.  Deliberately conservative: if a schema declares a
    type, a mismatched argument is rejected before any side effect happens.
    """
    if not isinstance(args, dict):
        return False, "arguments must be an object"
    schema_type = schema.get("type", "object")
    if schema_type != "object":
        return True, ""

    properties: dict[str, Any] = schema.get("properties", {}) or {}
    required: list[str] = schema.get("required", []) or []

    for name in required:
        if name not in args or args[name] in (None, ""):
            return False, f"missing required argument '{name}'"

    if schema.get("additionalProperties") is False:
        for key in args:
            if key not in properties:
                return False, f"unexpected argument '{key}'"

    type_map = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    required = set(schema.get("required", []) if isinstance(schema, dict) else [])
    for key, value in args.items():
        spec = properties.get(key)
        if not isinstance(spec, dict):
            continue
        if value is None and key not in required:
            # Models (and local intent parsing) often send optional arguments as
            # explicit nulls: treat that as "not provided" instead of failing.
            continue
        expected = spec.get("type")
        if expected and expected in type_map:
            py_type = type_map[expected]
            # bool is a subclass of int - reject it where an integer is expected
            if expected in ("integer", "number") and isinstance(value, bool):
                return False, f"argument '{key}' must be a number, not a boolean"
            if value is None and spec.get("nullable"):
                continue
            if not isinstance(value, py_type):
                return False, f"argument '{key}' must be of type {expected}"
        if "enum" in spec and value not in spec["enum"]:
            return False, f"argument '{key}' must be one of {spec['enum']}"
        if expected == "object" and isinstance(value, dict) and spec.get("properties"):
            ok, message = validate_arguments(spec, value)
            if not ok:
                return False, f"{key}.{message}"
    return True, ""


class ToolRegistry:
    def __init__(
        self,
        permissions: PermissionManager,
        audit: AuditLog,
        bus: EventBus | None = None,
    ) -> None:
        self.permissions = permissions
        self.audit = audit
        self.bus = bus or default_bus
        self._tools: dict[str, Tool] = {}

    # --------------------------------------------------------------- register
    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool
        return tool

    def register_function(self, **kwargs: Any) -> Callable:
        def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
            self.register(Tool(handler=function, **kwargs))
            return function

        return decorator

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    # ------------------------------------------------------------------ access
    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools)

    def by_category(self) -> dict[str, list[Tool]]:
        groups: dict[str, list[Tool]] = {}
        for tool in self._tools.values():
            groups.setdefault(tool.category, []).append(tool)
        return {key: sorted(value, key=lambda item: item.name) for key, value in sorted(groups.items())}

    def list_tools(self, category: str | None = None, level: int | None = None) -> list[dict[str, Any]]:
        tools = [tool for tool in self._tools.values() if category is None or tool.category == category]
        if level is not None:
            tools = [tool for tool in tools if tool.level <= level]
        return [tool.schema() for tool in sorted(tools, key=lambda item: item.name)]

    def openai_schemas(self, names: list[str] | None = None) -> list[dict[str, Any]]:
        selected = (
            [self._tools[name] for name in names if name in self._tools]
            if names
            else list(self._tools.values())
        )
        return [tool.openai_schema() for tool in selected]

    # ---------------------------------------------------------------- execute
    def call(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        actor: str = "ray",
        approval_id: int | None = None,
        timeout: float | None = None,
    ) -> ToolResult:
        started = time.time()
        args = args or {}
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, error=f"unknown tool '{name}'", tool=name)

        ok, message = validate_arguments(tool.parameters, args)
        if not ok:
            self.audit.record("tool_invalid_arguments", name, result="error", detail=message, level=tool.level)
            return ToolResult(False, error=message, tool=name, level=tool.level, duration_ms=_ms(started))

        decision = self.permissions.check(name, tool.level, args, reason=tool.description)
        if not decision.allowed:
            approved = approval_id is not None and self.permissions.is_approved(approval_id)
            if approved:
                log.info("tool '%s' running under your approval #%s", name, approval_id)
            else:
                self.audit.record(
                    "tool_blocked",
                    name,
                    target=_brief(args),
                    level=tool.level,
                    result="blocked",
                    detail=decision.reason,
                )
                return ToolResult(
                    False,
                    error=decision.reason,
                    tool=name,
                    level=tool.level,
                    requires_approval=decision.approval_id,
                    duration_ms=_ms(started),
                )

        if timeout and _accepts_timeout(tool.handler):
            args = {**args, "timeout": timeout}

        try:
            if inspect.iscoroutinefunction(tool.handler):
                import asyncio

                data = asyncio.run(tool.handler(**args))
            else:
                data = tool.handler(**args)
        except Exception as exc:
            log.error("tool '%s' failed: %s", name, exc)
            log.debug("%s", traceback.format_exc())
            self.audit.record(
                "tool_error", name, target=_brief(args), level=tool.level,
                result="error", detail=f"{exc.__class__.__name__}: {exc}",
            )
            self.bus.publish("tool_error", tool=name, error=str(exc), level=tool.level)
            return ToolResult(False, error=str(exc), tool=name, level=tool.level, duration_ms=_ms(started))

        verified: bool | None = None
        if tool.verify:
            try:
                verified, verification_detail = tool.verify(data)
                if not verified:
                    self.audit.record(
                        "tool_unverified", name, target=_brief(args), level=tool.level,
                        result="unverified", detail=verification_detail,
                    )
            except Exception as exc:  # verification must never crash a call
                verified = None
                log.warning("verification for '%s' failed: %s", name, exc)

        duration = _ms(started)
        self.audit.record(
            "tool_executed", name, target=_brief(args), level=tool.level, result="ok",
            detail=_summarise(data),
        )
        self.bus.publish(
            "tool_executed",
            tool=name,
            ok=True,
            level=tool.level,
            duration_ms=duration,
            actor=actor,
        )
        return ToolResult(
            True, data=data, tool=name, level=tool.level, verified=verified, duration_ms=duration
        )

    def describe(self) -> dict[str, Any]:
        return {
            "count": len(self._tools),
            "by_category": {
                category: [tool.name for tool in tools] for category, tools in self.by_category().items()
            },
            "levels": {str(level): sum(1 for t in self._tools.values() if t.level == level) for level in (1, 2, 3)},
        }


def _ms(started: float) -> int:
    return int((time.time() - started) * 1000)


def _brief(args: dict[str, Any]) -> str:
    try:
        text = json.dumps(args, default=str)
    except Exception:
        text = str(args)
    return text[:300]


def _summarise(data: Any) -> str:
    try:
        text = data if isinstance(data, str) else json.dumps(data, default=str)
    except Exception:
        text = str(data)
    return text[:500]


def _accepts_timeout(handler: Callable) -> bool:
    try:
        return "timeout" in inspect.signature(handler).parameters
    except (TypeError, ValueError):
        return False
