"""Windows (and Linux) system controls.

Every function is implemented against the real OS API - no simulated toggles.
Where a capability genuinely does not exist on a platform, RAY raises a clear
error instead of pretending to succeed.

Screenshots use GDI directly on Windows (no Pillow needed) and standard capture
tools on Linux.  Volume uses the OS media-key path, which works on every Windows
build without COM interop; absolute volume needs ``pycaw`` and says so.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path
from typing import Any

from ..core.logging_setup import get_logger

log = get_logger("computer.controls")

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")
IS_MAC = sys.platform == "darwin"

VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
KEYEVENTF_KEYUP = 0x0002


# --------------------------------------------------------------------- PNG
def _png_bytes(width: int, height: int, rgb: bytes) -> bytes:
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[row * stride:(row + 1) * stride] for row in range(height))
    compressed = zlib.compress(raw, 6)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


class SystemControl:
    def __init__(self, screenshot_dir: Path | str | None = None) -> None:
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else Path.home() / "RAY-screenshots"

    # ----------------------------------------------------------- screenshots
    def screenshot(
        self,
        region: tuple[int, int, int, int] | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        """Capture the screen (or a region) and write a PNG. Returns real dimensions."""
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        name = filename or f"screenshot-{time.strftime('%Y%m%d-%H%M%S')}.png"
        target = self.screenshot_dir / name
        if IS_WINDOWS:
            image_bytes, size = self._windows_capture(region)
        elif IS_MAC or IS_LINUX:
            image_bytes, size = self._unix_capture(region, target)
            if image_bytes is None:
                return {"path": str(target), "width": size[0], "height": size[1], "method": "external tool"}
        else:  # pragma: no cover
            raise RuntimeError("screenshots are not supported on this platform")
        if image_bytes is not None:
            target.write_bytes(image_bytes)
        if not target.exists():
            raise RuntimeError("the screenshot tool did not produce a file")
        return {
            "path": str(target),
            "width": size[0],
            "height": size[1],
            "size_bytes": target.stat().st_size,
            "region": list(region) if region else None,
        }

    def _windows_capture(self, region: tuple[int, int, int, int] | None) -> tuple[bytes, tuple[int, int]]:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        user32.SetProcessDPIAware()
        if region:
            left, top, width, height = region
        else:
            left, top = 0, 0
            width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
            height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
            if width <= 0 or height <= 0:
                width = user32.GetSystemMetrics(0)
                height = user32.GetSystemMetrics(1)
        if width <= 0 or height <= 0:
            raise RuntimeError("could not determine screen dimensions")

        screen_dc = user32.GetDC(0)
        if not screen_dc:
            raise RuntimeError("GetDC failed - no desktop session?")
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        previous = gdi32.SelectObject(memory_dc, bitmap)
        try:
            SRCCOPY = 0x00CC0020
            if not gdi32.BitBlt(memory_dc, 0, 0, width, height, screen_dc, left, top, SRCCOPY):
                raise RuntimeError("BitBlt failed while capturing the screen")
            info = _BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            info.bmiHeader.biWidth = width
            info.bmiHeader.biHeight = -height  # top-down rows
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            info.bmiHeader.biCompression = 0  # BI_RGB
            buffer_length = width * height * 4
            buffer = ctypes.create_string_buffer(buffer_length)
            copied = gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(info), 0)
            if not copied:
                raise RuntimeError("GetDIBits returned no scan lines")
            raw = buffer.raw
            rgb = bytearray(width * height * 3)
            rgb[0::3] = raw[2::4]
            rgb[1::3] = raw[1::4]
            rgb[2::3] = raw[0::4]
            return _png_bytes(width, height, bytes(rgb)), (width, height)
        finally:
            gdi32.SelectObject(memory_dc, previous)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(memory_dc)
            user32.ReleaseDC(0, screen_dc)

    def _unix_capture(
        self, region: tuple[int, int, int, int] | None, target: Path
    ) -> tuple[bytes | None, tuple[int, int]]:
        try:
            from PIL import ImageGrab  # type: ignore

            box = None
            if region:
                left, top, width, height = region
                box = (left, top, left + width, top + height)
            image = ImageGrab.grab(bbox=box)
            path = target.with_suffix(".png")
            image.save(path)
            return None, image.size
        except Exception:
            pass

        candidates: list[list[str]] = []
        if IS_MAC:
            candidates.append(["screencapture", "-x", str(target)])
            if region:
                left, top, width, height = region
                candidates.insert(0, ["screencapture", "-x", "-R", f"{left},{top},{width},{height}", str(target)])
        else:
            if shutil.which("grim"):
                candidates.append(["grim", str(target)])
            if shutil.which("scrot"):
                candidates.append(["scrot", "-o", str(target)])
            if shutil.which("gnome-screenshot"):
                candidates.append(["gnome-screenshot", "-f", str(target)])
            if shutil.which("import"):
                candidates.append(["import", "-window", "root", str(target)])
        for command in candidates:
            try:
                result = subprocess.run(command, capture_output=True, timeout=20)
                if result.returncode == 0 and target.exists():
                    return None, _png_size(target)
            except (OSError, subprocess.SubprocessError):
                continue
        raise RuntimeError(
            "No screenshot tool is available here. On Linux install one of: grim, scrot, "
            "gnome-screenshot, imagemagick, or Pillow. On Windows this uses native GDI."
        )

    # --------------------------------------------------------------- volume
    def volume_step(self, direction: str = "up", steps: int = 2) -> dict[str, Any]:
        direction = (direction or "up").lower()
        if direction not in {"up", "down", "mute"}:
            raise ValueError("direction must be 'up', 'down' or 'mute'")
        steps = max(1, min(int(steps), 20))
        if IS_WINDOWS:
            code = VK_VOLUME_UP if direction == "up" else VK_VOLUME_DOWN if direction == "down" else VK_VOLUME_MUTE
            presses = 1 if direction == "mute" else steps
            for _ in range(presses):
                ctypes.windll.user32.keybd_event(code, 0, 0, 0)
                ctypes.windll.user32.keybd_event(code, 0, KEYEVENTF_KEYUP, 0)
                time.sleep(0.02)
            return {"volume": direction, "steps": presses, "method": "media keys"}
        if IS_LINUX:
            tool = shutil.which("pactl") or shutil.which("amixer") or shutil.which("wpctl")
            if not tool:
                raise RuntimeError("no audio control tool found (pactl/amixer/wpctl)")
            step = max(1, min(steps * 5, 50))
            if tool.endswith("pactl"):
                action = {"up": f"+{step}%", "down": f"-{step}%"}.get(direction, "toggle")
                subprocess.run([tool, "set-sink-volume", "@DEFAULT_SINK@", action], check=True, timeout=10)
            elif tool.endswith("wpctl"):
                action = {"up": f"{step}%+", "down": f"{step}%-", "mute": "toggle"}[direction]
                subprocess.run([tool, "set-volume", "@DEFAULT_AUDIO_SINK@", action], check=True, timeout=10)
            else:
                control = "Master"
                subprocess.run(
                    [tool, "-q", "sset", control, f"{step}%" if direction == "up" else f"{step}%-" if direction == "down" else "toggle"],
                    check=True, timeout=10,
                )
            return {"volume": direction, "steps": steps, "method": tool.rsplit("/", 1)[-1]}
        if IS_MAC:
            action = {"up": "+", "down": "-", "mute": ""}[direction]
            script = f"set volume output volume ((output volume of (get volume settings)) {'+' if direction == 'up' else '-' if direction == 'down' else ''} {max(1, steps * 5)})" if direction != "mute" else "set volume output muted true"
            subprocess.run(["osascript", "-e", script], check=True, timeout=10)
            return {"volume": direction, "steps": steps, "method": "osascript"}
        raise RuntimeError("volume control is not supported on this platform")

    def set_volume(self, percent: int) -> dict[str, Any]:
        percent = max(0, min(100, int(percent)))
        if IS_WINDOWS:
            try:
                from pycaw.pycaw import AudioUtilities  # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    "Setting an exact volume level on Windows requires the optional 'pycaw' "
                    "package (pip install pycaw). Without it RAY can still step the volume up "
                    "and down with the media keys."
                ) from exc
            device = AudioUtilities.GetSpeakers()
            volume = device.EndpointVolume
            volume.SetMasterVolumeLevelScalar(percent / 100.0, None)
            return {"volume_percent": percent, "method": "pycaw"}
        if IS_LINUX:
            tool = shutil.which("pactl")
            if tool:
                subprocess.run([tool, "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"], check=True, timeout=10)
                return {"volume_percent": percent, "method": "pactl"}
            tool = shutil.which("amixer")
            if tool:
                subprocess.run([tool, "-q", "sset", "Master", f"{percent}%"], check=True, timeout=10)
                return {"volume_percent": percent, "method": "amixer"}
        if IS_MAC:
            subprocess.run(["osascript", "-e", f"set volume output volume {percent}"], check=True, timeout=10)
            return {"volume_percent": percent, "method": "osascript"}
        raise RuntimeError("exact volume control is not supported on this platform")

    # ------------------------------------------------------------- brightness
    def set_brightness(self, percent: int) -> dict[str, Any]:
        percent = max(0, min(100, int(percent)))
        if IS_WINDOWS:
            script = (
                f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
                f".WmiSetBrightness(1,{percent})"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    "Windows did not accept a brightness change. Internal laptop panels usually "
                    "support this; external monitors generally do not. "
                    f"{(result.stderr or '').strip()[:200]}"
                )
            return {"brightness_percent": percent, "method": "wmi"}
        if IS_LINUX:
            tool = shutil.which("brightnessctl")
            if tool:
                subprocess.run([tool, "set", f"{percent}%"], check=True, timeout=10)
                return {"brightness_percent": percent, "method": "brightnessctl"}
            backlights = sorted(Path("/sys/class/backlight").glob("*")) if Path("/sys/class/backlight").exists() else []
            if not backlights:
                raise RuntimeError("No backlight device found (desktop or permissions missing)")
            device = backlights[0]
            maximum = int((device / "max_brightness").read_text().strip() or 100)
            value = round(maximum * percent / 100)
            try:
                (device / "brightness").write_text(str(value))
            except PermissionError as exc:
                raise RuntimeError(
                    "Changing screen brightness needs permission on /sys/class/backlight. "
                    "Install 'brightnessctl' or adjust udev rules."
                ) from exc
            return {"brightness_percent": percent, "method": str(device)}
        if IS_MAC:
            subprocess.run(["osascript", "-e", f"tell application \"System Events\" to key code 145"], check=False)
            raise RuntimeError("Brightness control on macOS requires a third-party tool (e.g. 'brightness')")
        raise RuntimeError("brightness control is not supported on this platform")

    # --------------------------------------------------------- power actions
    def lock(self) -> dict[str, Any]:
        if IS_WINDOWS:
            if not ctypes.windll.user32.LockWorkStation():
                raise RuntimeError("Windows refused to lock the workstation")
            return {"action": "lock", "done": True}
        if IS_LINUX:
            for command in (["loginctl", "lock-session"], ["xdg-screensaver", "lock"], ["gnome-screensaver-command", "-l"]):
                if shutil.which(command[0]):
                    subprocess.run(command, check=False, timeout=10)
                    return {"action": "lock", "done": True, "method": command[0]}
            raise RuntimeError("no screen locking tool found")
        if IS_MAC:
            subprocess.run(["pmset", "displaysleepnow"], check=False)
            return {"action": "lock", "done": True}
        raise RuntimeError("locking is not supported on this platform")

    def sleep(self) -> dict[str, Any]:
        if IS_WINDOWS:
            if not ctypes.windll.powrprof.SetSuspendState(0, 1, 0):
                raise RuntimeError("Windows refused the sleep request")
            return {"action": "sleep", "done": True}
        if IS_LINUX:
            subprocess.run(["systemctl", "suspend"], check=True, timeout=15)
            return {"action": "sleep", "done": True}
        if IS_MAC:
            subprocess.run(["pmset", "sleepnow"], check=True, timeout=15)
            return {"action": "sleep", "done": True}
        raise RuntimeError("sleep is not supported on this platform")

    def restart(self, delay_seconds: int = 5) -> dict[str, Any]:
        delay_seconds = max(0, min(int(delay_seconds), 120))
        if IS_WINDOWS:
            subprocess.run(["shutdown", "/r", "/t", str(delay_seconds)], check=True, timeout=15)
        elif IS_LINUX:
            subprocess.run(["systemctl", "reboot"], check=True, timeout=15)
        elif IS_MAC:
            subprocess.run(["osascript", "-e", 'tell application "System Events" to restart'], check=True, timeout=15)
        else:
            raise RuntimeError("restart is not supported on this platform")
        return {"action": "restart", "delay_seconds": delay_seconds, "done": True}

    def shutdown(self, delay_seconds: int = 5) -> dict[str, Any]:
        delay_seconds = max(0, min(int(delay_seconds), 120))
        if IS_WINDOWS:
            subprocess.run(["shutdown", "/s", "/t", str(delay_seconds)], check=True, timeout=15)
        elif IS_LINUX:
            subprocess.run(["systemctl", "poweroff"], check=True, timeout=15)
        elif IS_MAC:
            subprocess.run(["osascript", "-e", 'tell application "System Events" to shut down'], check=True, timeout=15)
        else:
            raise RuntimeError("shutdown is not supported on this platform")
        return {"action": "shutdown", "delay_seconds": delay_seconds, "done": True}

    def cancel_shutdown(self) -> dict[str, Any]:
        if IS_WINDOWS:
            subprocess.run(["shutdown", "/a"], check=True, timeout=15)
            return {"action": "cancel_shutdown", "done": True}
        raise RuntimeError("Windows-only: cancelling a pending shutdown")

    # ------------------------------------------------------------ OS panels
    def open_panel(self, panel: str) -> dict[str, Any]:
        panel = (panel or "").lower().strip()
        if IS_WINDOWS:
            uris = {
                "settings": "ms-settings:",
                "display": "ms-settings:display",
                "sound": "ms-settings:sound",
                "network": "ms-settings:network",
                "bluetooth": "ms-settings:bluetooth",
                "privacy": "ms-settings:privacy",
                "windows_update": "ms-settings:windowsupdate",
                "power": "ms-settings:powersleep",
                "apps": "ms-settings:appsfeatures",
                "storage": "ms-settings:storagesense",
                "about": "ms-settings:about",
            }
            if panel in uris:
                os.startfile(uris[panel])  # type: ignore[attr-defined]
                return {"opened": panel, "uri": uris[panel]}
            if panel in ("task_manager", "taskmanager"):
                subprocess.Popen(["taskmgr.exe"])
                return {"opened": "Task Manager"}
            if panel in ("terminal", "cmd", "powershell"):
                executable = "wt.exe" if panel == "terminal" and shutil.which("wt.exe") else (
                    "powershell.exe" if panel == "powershell" else "cmd.exe"
                )
                subprocess.Popen([executable], creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
                return {"opened": executable}
            if panel == "control_panel":
                subprocess.Popen(["control.exe"])
                return {"opened": "Control Panel"}
            if panel == "task_scheduler":
                subprocess.Popen(["taskschd.msc"])
                return {"opened": "Task Scheduler"}
            raise ValueError(f"unknown panel '{panel}'")
        if IS_LINUX:
            mapping = {
                "settings": ["gnome-control-center"],
                "display": ["gnome-control-center", "display"],
                "sound": ["gnome-control-center", "sound"],
                "network": ["gnome-control-center", "network"],
                "task_manager": ["gnome-system-monitor"],
                "terminal": [os.environ.get("TERMINAL", "x-terminal-emulator")],
            }
            command = mapping.get(panel)
            if not command:
                raise ValueError(f"unknown panel '{panel}'")
            if not shutil.which(command[0]):
                raise RuntimeError(f"'{command[0]}' is not installed on this system")
            subprocess.Popen(command, start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"opened": panel, "command": command[0]}
        if IS_MAC:
            if panel in ("settings", "control_panel"):
                subprocess.Popen(["open", "-b", "com.apple.systempreferences"])
                return {"opened": "System Settings"}
            if panel == "task_manager":
                subprocess.Popen(["open", "-a", "Activity Monitor"])
                return {"opened": "Activity Monitor"}
            if panel == "terminal":
                subprocess.Popen(["open", "-a", "Terminal"])
                return {"opened": "Terminal"}
            raise ValueError(f"unknown panel '{panel}'")
        raise RuntimeError("system panels are not supported on this platform")

    # ----------------------------------------------------------- clipboard
    def clipboard_get(self) -> str:
        try:
            import pyperclip  # type: ignore

            return pyperclip.paste() or ""
        except Exception:
            pass
        try:
            import tkinter

            root = tkinter.Tk()
            root.withdraw()
            try:
                return root.clipboard_get() or ""
            finally:
                root.destroy()
        except Exception as exc:
            raise RuntimeError(
                f"Clipboard is not reachable ({exc.__class__.__name__}). "
                "Install 'pyperclip' for reliable clipboard access."
            ) from exc

    def clipboard_set(self, text: str) -> dict[str, Any]:
        try:
            import pyperclip  # type: ignore

            pyperclip.copy(text)
            return {"copied_chars": len(text), "method": "pyperclip"}
        except Exception:
            pass
        try:
            import tkinter

            root = tkinter.Tk()
            root.withdraw()
            try:
                root.clipboard_clear()
                root.clipboard_append(text)
                root.update()
            finally:
                root.destroy()
            return {"copied_chars": len(text), "method": "tkinter"}
        except Exception as exc:
            raise RuntimeError(f"could not write to the clipboard: {exc}") from exc


# ------------------------------------------------------------ input automation
class InputControl:
    """Keyboard and mouse automation.

    Prefers ``pynput`` when installed; on Windows falls back to native SendInput
    so typing works out of the box.  Text is injected as unicode events, never
    through a shell, so nothing is interpreted as a command.
    """

    def __init__(self) -> None:
        self._pynput = None
        try:  # optional dependency
            import pynput  # type: ignore

            self._pynput = pynput
        except Exception:
            self._pynput = None

    def available(self) -> tuple[bool, str]:
        if self._pynput is not None:
            return True, "pynput"
        if IS_WINDOWS:
            return True, "native SendInput"
        return False, "install 'pynput' to enable keyboard/mouse automation on this platform"

    def type_text(self, text: str, interval: float = 0.01) -> dict[str, Any]:
        if not text:
            return {"typed_chars": 0}
        if self._pynput is not None:
            from pynput.keyboard import Controller

            keyboard = Controller()
            for char in text:
                if char == "\n":
                    keyboard.press("enter")
                    keyboard.release("enter")
                else:
                    keyboard.type(char)
                time.sleep(max(0.0, interval))
            return {"typed_chars": len(text), "method": "pynput"}
        if IS_WINDOWS:
            self._windows_type(text)
            return {"typed_chars": len(text), "method": "SendInput"}
        raise RuntimeError("keyboard automation needs the optional 'pynput' package here")

    @staticmethod
    def _windows_type(text: str) -> None:
        user32 = ctypes.windll.user32
        INPUT_KEYBOARD = 1
        KEYEVENTF_UNICODE = 0x0004

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class _INPUTunion(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ulong), ("union", _INPUTunion)]

        def send(char: str) -> None:
            code = ord(char)
            down = INPUT(type=INPUT_KEYBOARD)
            down.union.ki = KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, None)
            up = INPUT(type=INPUT_KEYBOARD)
            up.union.ki = KEYBDINPUT(0, code, KEYEVENTF_UNICODE | 0x0002, 0, None)
            user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
            user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))

        for char in text:
            send(char)
            time.sleep(0.005)

    def hotkey(self, *keys: str) -> dict[str, Any]:
        if not keys:
            raise ValueError("no keys given")
        if self._pynput is None:
            raise RuntimeError("hotkeys need the optional 'pynput' package here")
        from pynput.keyboard import Controller, Key

        special = {
            "ctrl": Key.ctrl, "control": Key.ctrl, "alt": Key.alt, "shift": Key.shift,
            "win": Key.cmd, "cmd": Key.cmd, "enter": Key.enter, "esc": Key.esc,
            "tab": Key.tab, "space": Key.space, "delete": Key.delete, "backspace": Key.backspace,
            "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right,
        }
        keyboard = Controller()
        resolved = [special.get(key.lower(), key) for key in keys]
        for key in resolved:
            keyboard.press(key)
        for key in reversed(resolved):
            keyboard.release(key)
        return {"hotkey": "+".join(keys)}

    def move_mouse(self, x: int, y: int) -> dict[str, Any]:
        if self._pynput is not None:
            from pynput.mouse import Controller

            Controller().position = (int(x), int(y))
            return {"x": int(x), "y": int(y), "method": "pynput"}
        if IS_WINDOWS:
            ctypes.windll.user32.SetCursorPos(int(x), int(y))
            return {"x": int(x), "y": int(y), "method": "SetCursorPos"}
        raise RuntimeError("mouse control needs the optional 'pynput' package here")

    def click(self, button: str = "left", double: bool = False) -> dict[str, Any]:
        if self._pynput is not None:
            from pynput.mouse import Button, Controller

            mapping = {"left": Button.left, "right": Button.right, "middle": Button.middle}
            mouse = Controller()
            target = mapping.get(button.lower(), Button.left)
            mouse.click(target, 2 if double else 1)
            return {"clicked": button, "double": double}
        if IS_WINDOWS:
            user32 = ctypes.windll.user32
            flags = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}
            down, up = flags.get(button.lower(), flags["left"])
            repeats = 2 if double else 1
            for _ in range(repeats):
                user32.mouse_event(down, 0, 0, 0, 0)
                user32.mouse_event(up, 0, 0, 0, 0)
                time.sleep(0.08)
            return {"clicked": button, "double": double}
        raise RuntimeError("mouse clicks need the optional 'pynput' package here")


def _png_size(path: Path) -> tuple[int, int]:
    try:
        with path.open("rb") as handle:
            header = handle.read(33)
        if header[:8] != b"\x89PNG\r\n\x1a\n":
            return (0, 0)
        width, height = struct.unpack(">II", header[16:24])
        return (width, height)
    except OSError:
        return (0, 0)
