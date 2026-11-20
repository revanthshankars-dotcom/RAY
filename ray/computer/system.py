"""System monitor - real hardware data only.

Everything here comes from psutil / the OS. Nothing is estimated, synthesised
or padded: if the platform cannot report a value (battery health, temperature
on most desktops) the field is ``None`` with a reason instead of a fake number.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import sys
import time
from collections import deque
from typing import Any

from ..core.logging_setup import get_logger

log = get_logger("computer.system")

try:  # pragma: no cover - psutil is a declared dependency
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore


class SystemMonitor:
    def __init__(self, history_size: int = 120) -> None:
        self._history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._last_net: tuple[float, int, int] | None = None
        self._last_sample: float | None = None

    # ------------------------------------------------------------------ basics
    @staticmethod
    def _require_psutil() -> None:
        if psutil is None:
            raise RuntimeError(
                "psutil is required for system monitoring. Install it with: pip install psutil"
            )

    def identity(self) -> dict[str, Any]:
        return {
            "hostname": socket.gethostname(),
            "platform": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor() or "unknown",
            "python": sys.version.split()[0],
            "user": os.environ.get("USERNAME") or os.environ.get("USER") or "user",
        }

    def cpu(self) -> dict[str, Any]:
        self._require_psutil()
        freq = None
        try:
            freq_info = psutil.cpu_freq()
            if freq_info:
                freq = {"current_mhz": round(freq_info.current, 1), "max_mhz": round(freq_info.max, 1)}
        except (AttributeError, NotImplementedError):
            freq = None
        load = None
        try:
            load = [round(value, 2) for value in os.getloadavg()]
        except (AttributeError, OSError):
            load = None
        return {
            "percent": psutil.cpu_percent(interval=0.15),
            "cores_logical": psutil.cpu_count(logical=True),
            "cores_physical": psutil.cpu_count(logical=False),
            "frequency": freq,
            "load_average": load,
        }

    def memory(self) -> dict[str, Any]:
        self._require_psutil()
        virtual = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return {
            "total_bytes": virtual.total,
            "available_bytes": virtual.available,
            "used_bytes": virtual.used,
            "percent": virtual.percent,
            "swap_total_bytes": swap.total,
            "swap_used_bytes": swap.used,
            "swap_percent": swap.percent,
        }

    def disks(self) -> list[dict[str, Any]]:
        self._require_psutil()
        disks: list[dict[str, Any]] = []
        seen: set[str] = set()
        for partition in psutil.disk_partitions(all=False):
            if partition.mountpoint in seen:
                continue
            seen.add(partition.mountpoint)
            try:
                usage = shutil.disk_usage(partition.mountpoint)
            except (PermissionError, OSError):
                continue
            if usage.total == 0:
                continue
            disks.append(
                {
                    "mount": partition.mountpoint,
                    "device": partition.device,
                    "filesystem": partition.fstype,
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "free_bytes": usage.free,
                    "percent": round(usage.used / usage.total * 100, 1),
                }
            )
        return disks

    def network(self) -> dict[str, Any]:
        self._require_psutil()
        counters = psutil.net_io_counters()
        now = time.time()
        download_bps = upload_bps = 0.0
        if self._last_net:
            last_time, last_sent, last_recv = self._last_net
            elapsed = max(now - last_time, 0.001)
            download_bps = max(0.0, (counters.bytes_recv - last_recv) / elapsed)
            upload_bps = max(0.0, (counters.bytes_sent - last_sent) / elapsed)
        self._last_net = (now, counters.bytes_sent, counters.bytes_recv)
        addresses: list[str] = []
        try:
            for name, addrs in psutil.net_if_addrs().items():
                for addr in addrs:
                    if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                        addresses.append(f"{name}: {addr.address}")
        except Exception:  # pragma: no cover
            pass
        return {
            "bytes_sent": counters.bytes_sent,
            "bytes_recv": counters.bytes_recv,
            "upload_bps": round(upload_bps, 1),
            "download_bps": round(download_bps, 1),
            "addresses": addresses,
            "hostname": socket.gethostname(),
        }

    def battery(self) -> dict[str, Any] | None:
        self._require_psutil()
        try:
            battery = psutil.sensors_battery()
        except (AttributeError, NotImplementedError):
            return None
        if battery is None:
            return None
        seconds = battery.secsleft
        minutes_left = None
        if seconds not in (None, psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN) and seconds > 0:
            minutes_left = int(seconds // 60)
        return {
            "percent": round(battery.percent, 1),
            "plugged": bool(battery.power_plugged),
            "minutes_left": minutes_left,
            "health_note": "Battery wear/health is not exposed by this platform; use vendor tools.",
        }

    def temperatures(self) -> dict[str, Any]:
        self._require_psutil()
        if not hasattr(psutil, "sensors_temperatures"):
            return {"available": False, "reason": "not supported on this platform", "sensors": []}
        try:
            sensors = psutil.sensors_temperatures()
        except Exception as exc:  # pragma: no cover
            return {"available": False, "reason": str(exc), "sensors": []}
        if not sensors:
            return {
                "available": False,
                "reason": "no temperature sensor exposed to user space (normal on Windows)",
                "sensors": [],
            }
        readings = []
        for chip, entries in sensors.items():
            for entry in entries:
                readings.append(
                    {
                        "chip": chip,
                        "label": entry.label or chip,
                        "current_c": round(entry.current, 1),
                        "high_c": entry.high,
                        "critical_c": entry.critical,
                    }
                )
        return {"available": True, "reason": "", "sensors": readings}

    def uptime(self) -> dict[str, Any]:
        self._require_psutil()
        boot = psutil.boot_time()
        seconds = max(0, int(time.time() - boot))
        return {
            "boot_time": boot,
            "seconds": seconds,
            "human": f"{seconds // 86400}d {seconds % 86400 // 3600}h {seconds % 3600 // 60}m",
        }

    # ------------------------------------------------------------------ views
    def snapshot(self) -> dict[str, Any]:
        self._require_psutil()
        data: dict[str, Any] = {
            "timestamp": time.time(),
            "identity": self.identity(),
            "cpu": self.cpu(),
            "memory": self.memory(),
            "disks": self.disks(),
            "network": self.network(),
            "battery": self.battery(),
            "uptime": self.uptime(),
        }
        try:
            data["temperatures"] = self.temperatures()
        except Exception:
            data["temperatures"] = {"available": False, "reason": "unavailable", "sensors": []}
        self._history.append(
            {
                "ts": data["timestamp"],
                "cpu": data["cpu"]["percent"],
                "memory": data["memory"]["percent"],
                "download_bps": data["network"]["download_bps"],
                "upload_bps": data["network"]["upload_bps"],
                "disk_percent": data["disks"][0]["percent"] if data["disks"] else 0,
            }
        )
        data["warnings"] = self.warnings(data)
        return data

    def history(self, limit: int = 60) -> list[dict[str, Any]]:
        return list(self._history)[-limit:]

    def warnings(self, snapshot: dict[str, Any] | None = None) -> list[str]:
        data = snapshot or (self._history[-1] if self._history else {})
        messages: list[str] = []
        if not data:
            return messages
        cpu = data.get("cpu")
        if isinstance(cpu, dict) and cpu.get("percent", 0) > 90:
            messages.append(f"CPU is at {cpu['percent']:.0f}% - something is working hard")
        elif isinstance(cpu, (int, float)) and cpu > 90:
            messages.append(f"CPU is at {cpu:.0f}%")
        memory = data.get("memory")
        if isinstance(memory, dict) and memory.get("percent", 0) > 88:
            messages.append(f"Memory is at {memory['percent']:.0f}% - close it or close apps")
        disks = data.get("disks") or []
        for disk in disks:
            if disk.get("percent", 0) > 92:
                messages.append(f"Disk {disk.get('mount')} is {disk['percent']:.0f}% full")
        battery = data.get("battery")
        if isinstance(battery, dict) and not battery.get("plugged") and battery.get("percent", 100) < 20:
            messages.append(f"Battery low ({battery['percent']:.0f}%)")
        return messages

    def processes(self, limit: int = 20, sort_by: str = "cpu") -> list[dict[str, Any]]:
        self._require_psutil()
        rows: list[dict[str, Any]] = []
        for process in psutil.process_iter(["pid", "name", "username", "memory_info", "cpu_percent", "create_time"]):
            try:
                info = process.info
                memory = info.get("memory_info")
                rows.append(
                    {
                        "pid": info["pid"],
                        "name": info.get("name") or "?",
                        "user": info.get("username") or "",
                        "cpu_percent": round(process.cpu_percent(interval=None), 1),
                        "memory_mb": round((memory.rss if memory else 0) / (1024 * 1024), 1),
                        "started": info.get("create_time"),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        key = "memory_mb" if sort_by == "memory" else "cpu_percent"
        rows.sort(key=lambda row: row.get(key, 0) or 0, reverse=True)
        return rows[: max(1, min(limit, 200))]

    def report(self) -> str:
        snapshot = self.snapshot()
        identity = snapshot["identity"]
        lines = [
            f"RAY system report - {time.strftime('%Y-%m-%d %H:%M')}",
            f"Host: {identity['hostname']} ({identity['platform']} {identity['release']}, {identity['machine']})",
            f"Python: {identity['python']}",
            "",
            f"CPU: {snapshot['cpu']['percent']:.1f}% of {snapshot['cpu']['cores_logical']} logical cores",
            f"RAM: {_gb(snapshot['memory']['used_bytes'])} / {_gb(snapshot['memory']['total_bytes'])} GB "
            f"({snapshot['memory']['percent']:.0f}%)",
        ]
        for disk in snapshot["disks"]:
            lines.append(
                f"Disk {disk['mount']}: {_gb(disk['used_bytes'])} / {_gb(disk['total_bytes'])} GB "
                f"({disk['percent']:.0f}% used, {_gb(disk['free_bytes'])} GB free)"
            )
        network = snapshot["network"]
        lines.append(
            f"Network: down {_rate(network['download_bps'])} / up {_rate(network['upload_bps'])}"
        )
        battery = snapshot.get("battery")
        if battery:
            lines.append(
                f"Battery: {battery['percent']:.0f}% "
                f"({'charging' if battery['plugged'] else 'on battery'})"
            )
        lines.append(f"Uptime: {snapshot['uptime']['human']}")
        for warning in snapshot.get("warnings", []):
            lines.append(f"! {warning}")
        top = self.processes(limit=5)
        if top:
            lines.append("")
            lines.append("Top processes by CPU:")
            for process in top:
                lines.append(
                    f"  {process['name'][:28]:28} pid {process['pid']:<6} "
                    f"cpu {process['cpu_percent']:5.1f}%  ram {process['memory_mb']:6.1f} MB"
                )
        return "\n".join(lines)


def _gb(value: float | int | None) -> str:
    if not value:
        return "0.0"
    return f"{value / (1024 ** 3):.1f}"


def _rate(bps: float) -> str:
    if bps < 1024:
        return f"{bps:.0f} B/s"
    if bps < 1024 ** 2:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps / (1024 ** 2):.1f} MB/s"
