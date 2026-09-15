"""Entry point, CLI flags and single-instance guard."""

from __future__ import annotations

import argparse
import ctypes
import sys

from . import config
from .logging_setup import setup

ERROR_ALREADY_EXISTS = 183

# Per-monitor-aware v2. Anything else and Windows lies to us about the screen.
DPI_AWARENESS_PER_MONITOR_V2 = -4


def declare_dpi_aware() -> int | None:
    """Opt out of DPI virtualisation. Returns the true tray icon size.

    This matters more than it sounds. Without it, on a display at 150%
    scaling Windows reports a 1280x720 screen and SM_CXSMICON of 16 -- then
    takes our 16px badge and stretches it to the 24px the tray actually uses.
    A 1.5x upscale of hand-plotted pixels is exactly the mush it sounds like,
    and it silently undid the work of drawing crisp glyphs at all.

    Declared here, before any window exists, because the awareness of a
    process cannot be changed once one has been created.
    """
    try:
        user32 = ctypes.windll.user32
        before = user32.GetSystemMetrics(49)          # SM_CXSMICON
        if not user32.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(DPI_AWARENESS_PER_MONITOR_V2)):
            return None
        after = user32.GetSystemMetrics(49)
        if after != before:
            log_dpi(before, after, user32.GetDpiForSystem())
        return after
    except (AttributeError, OSError):
        return None                # pre-1703 Windows: nothing to do


def log_dpi(before: int, after: int, dpi: int) -> None:
    from .logging_setup import get
    get("app").info("DPI-aware: tray icon is %dpx, not %dpx (%d dpi, %.0f%% "
                    "scaling) -- rendering at the real size",
                    after, before, dpi, dpi / 96.0 * 100)


def _single_instance() -> bool:
    """True if we are the only instance. Cheaper than a PID file."""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW(None, False, config.MUTEX_NAME)
        return kernel32.GetLastError() != ERROR_ALREADY_EXISTS
    except (AttributeError, OSError):
        return True


def _parse_fake(values: list[str] | None) -> dict[str, float]:
    """--fake-pct 5h=52 7d=80 -> {'five_hour': 52.0, 'seven_day': 80.0}"""
    aliases = {"5h": "five_hour", "session": "five_hour",
               "7d": "seven_day", "weekly": "seven_day"}
    out: dict[str, float] = {}
    for item in values or []:
        if "=" not in item:
            raise SystemExit("--fake-pct expects NAME=PCT, got: {0}".format(item))
        name, _, pct = item.partition("=")
        key = aliases.get(name.strip(), name.strip())
        try:
            out[key] = float(pct)
        except ValueError:
            raise SystemExit("--fake-pct value must be a number: {0}".format(item))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="clausage_bar",
        description="Windows tray monitor for Claude session and weekly usage.")
    ap.add_argument("--once", action="store_true",
                    help="run a single poll cycle and exit (no tray)")
    ap.add_argument("--diagnose", action="store_true",
                    help="with --once, print the merged snapshot, badge and tooltip")
    ap.add_argument("--no-toast", action="store_true",
                    help="never show notifications (log them instead)")
    ap.add_argument("--fake-pct", nargs="+", metavar="NAME=PCT",
                    help="override a window's percentage, e.g. 5h=82")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="also log to stderr")
    # The tray opens the panel and the widget as separate processes, which it
    # used to do with `pythonw -m clausage_bar.panel`. A frozen build has no
    # interpreter to hand `-m` to -- sys.executable is the app itself -- so the
    # one executable has to be able to answer as any of its surfaces. These
    # flags are that routing, and they work identically unfrozen.
    ap.add_argument("--panel", action="store_true",
                    help="open the usage panel instead of the tray")
    ap.add_argument("--widget", action="store_true",
                    help="open the floating widget instead of the tray")
    args = ap.parse_args(argv)

    # The tray renders Unicode fine; only a legacy console needs coaxing.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    setup(verbose=args.verbose or args.diagnose)

    # Before anything creates a window, and before the badge size is measured.
    if config.DPI_AWARE:
        declare_dpi_aware()

    # Dispatched after DPI awareness is declared, since both surfaces draw.
    if args.panel:
        from .panel import main as panel_main
        return panel_main()
    if args.widget:
        from .widget import main as widget_main
        return widget_main()

    from .app import TrayApp

    app = TrayApp(enable_toasts=not args.no_toast,
                  fake=_parse_fake(args.fake_pct))

    if args.once or args.diagnose:
        return app.run_once(diagnose=args.diagnose)

    if not _single_instance():
        print("clausage_bar is already running.", file=sys.stderr)
        return 0

    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
