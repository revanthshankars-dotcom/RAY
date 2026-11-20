#!/usr/bin/env python3
"""RAY - Real-time Autonomous Yourself.

Start the personal AI operating system:

    python main.py                 # desktop window on Windows, browser elsewhere
    python main.py --browser       # force the browser interface
    python main.py --headless      # engine + API only (no window)
    python main.py --check         # environment / dependency report
    python main.py --port 9000     # a different port

Everything RAY stores lives in one folder (see --check for the exact path).
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAY - personal AI operating system")
    parser.add_argument("--home", help="override the RAY data folder (default: %%LOCALAPPDATA%%\\RAY)")
    parser.add_argument("--host", help="interface bind address (default from settings: 127.0.0.1)")
    parser.add_argument("--port", type=int, help="interface port (default 8765)")
    parser.add_argument("--desktop", action="store_true", help="open the native PySide6 window")
    parser.add_argument("--browser", action="store_true", help="open the interface in your browser")
    parser.add_argument("--headless", action="store_true", help="run the engine without opening a window")
    parser.add_argument("--no-open", action="store_true", help="do not open a window or browser")
    parser.add_argument("--check", action="store_true", help="print an environment report and exit")
    parser.add_argument("--log-level", default="INFO", help="DEBUG, INFO, WARNING or ERROR")
    return parser.parse_args(argv)


def environment_report(home: str | None) -> int:
    from ray import __version__
    from ray.core.paths import Paths
    from ray.desktop.shell import check_dependencies

    paths = Paths(home) if home else Paths()
    print(f"RAY {__version__}")
    print(f"Python          : {sys.version.split()[0]} ({sys.executable})")
    print(f"Platform        : {sys.platform}")
    print(f"Data folder     : {paths.home}")
    print(f"Database        : {paths.db_path}")
    print(f"Logs            : {paths.logs}")

    checks: list[tuple[str, bool, str]] = []
    for module, purpose in (
        ("psutil", "system monitoring"),
        ("httpx", "AI provider calls"),
        ("cryptography", "encrypted credential vault"),
        ("pypdf", "PDF reading"),
    ):
        try:
            __import__(module)
            checks.append((module, True, purpose))
        except ImportError:
            checks.append((module, False, purpose))

    print("\nRequired packages:")
    for module, present, purpose in checks:
        print(f"  {'OK  ' if present else 'MISS'} {module:14} {purpose}")

    desktop_ok, desktop_detail = check_dependencies()
    print(f"\nDesktop window  : {'available' if desktop_ok else 'unavailable - ' + desktop_detail}")
    for optional, purpose in (
        ("PIL", "image metadata"), ("mutagen", "audio tags"), ("pynput", "keyboard/mouse automation"),
        ("pyperclip", "clipboard"), ("faster_whisper", "offline speech-to-text"),
    ):
        try:
            __import__(optional)
            print(f"  OK   {optional:16} {purpose}")
        except ImportError:
            print(f"  --   {optional:16} {purpose} (optional)")

    api_keys = {
        name: bool(os.environ.get(env))
        for name, env in (("grok", "XAI_API_KEY"), ("gemini", "GEMINI_API_KEY"), ("fish", "FISH_AUDIO_API_KEY"))
    }
    print("\nProvider keys in environment:", ", ".join(f"{k}={'set' if v else 'unset'}" for k, v in api_keys.items()))
    print("(Keys entered in the RAY interface are stored encrypted in the data folder.)")
    missing = [module for module, present, _ in checks if not present]
    if missing:
        print(f"\nInstall missing packages:  pip install {' '.join(missing)}")
        return 1
    print("\nEnvironment is ready. Run: python main.py")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.check:
        return environment_report(args.home)

    from ray.app import RayApp
    from ray.server.app import create_server, find_free_port

    app = RayApp(home=args.home, log_level=args.log_level)
    host = args.host or str(app.config.get("ui.host", "127.0.0.1"))
    port = args.port or int(app.config.get("ui.port", 8765))
    if port and not args.port:
        probe = find_free_port(host, port)
        if probe != port:
            from ray.core.logging_setup import get_logger

            get_logger("main").warning("port %s busy, using %s", port, probe)
            port = probe or 0

    server = create_server(app, host=host, port=port)
    server.start()
    app.start()

    url = server.url
    print("\n" + "=" * 66)
    print(f"  RAY is running at {url}")
    print(f"  Data folder: {app.paths.home}")
    print("  Press Ctrl+C to stop RAY cleanly.")
    print("=" * 66 + "\n")
    if server.exposed:
        print("  NOTE: the interface is bound beyond localhost. Anyone who can reach this")
        print("        machine on the network can use it. Keep it on 127.0.0.1 normally.\n")

    use_desktop = args.desktop or (
        not (args.browser or args.headless or args.no_open) and sys.platform.startswith("win")
    )

    def shutdown(*_args) -> None:
        try:
            server.stop()
        finally:
            app.stop()
        os._exit(0)

    signal.signal(signal.SIGINT, shutdown)
    try:
        signal.signal(signal.SIGTERM, shutdown)
    except (AttributeError, ValueError):  # pragma: no cover - Windows quirks
        pass

    opened = False
    if use_desktop:
        from ray.desktop.shell import DesktopUnavailable, run_desktop

        try:
            opened = True
            code = run_desktop(url, app_name="RAY")
            server.stop()
            app.stop()
            return code
        except DesktopUnavailable as exc:
            print(f"[desktop] {exc}")
            print("[desktop] Falling back to your browser.\n")

    if not opened and not args.no_open and not args.headless:
        import webbrowser

        threading.Thread(
            target=lambda: (time.sleep(0.6), webbrowser.open(url)), name="ray-browser", daemon=True
        ).start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    sys.exit(main())
