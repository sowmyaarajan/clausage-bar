"""Paths, tunables and environment overrides.

Every interval here is deliberately conservative: the usage endpoint is
undocumented and aggressively rate limited, so we poll politely.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------- packaging
#
# True in the standalone build, where the app is one .exe carrying its own
# Python. Three things behave differently there, and all three fail silently
# rather than loudly, which is why this is checked explicitly rather than
# inferred at each site:
#
#   * `sys.executable` is the app itself, not an interpreter, so it accepts
#     neither `-m module` nor `-c code`. Both were used to open the panel and
#     to probe the autostart target.
#   * There is no `pythonw.exe` beside it to switch to; the build is already
#     windowless.
#   * `run_tray.pyw` does not exist, so the autostart shortcut had nothing to
#     point at.
FROZEN = bool(getattr(sys, "frozen", False))

# ---------------------------------------------------------------- paths

HOME = Path.home()
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (HOME / ".claude"))
CLAUSAGE_DIR = CLAUDE_DIR / "clausage"

CREDENTIALS_FILE = CLAUDE_DIR / ".credentials.json"
CLAUDE_JSON = HOME / ".claude.json"          # account tier metadata
SESSIONS_DIR = CLAUDE_DIR / "sessions"        # live session registry

STATE_FILE = CLAUSAGE_DIR / "state.json"          # written by the Node collector
SNAPSHOT_FILE = CLAUSAGE_DIR / "snapshot.json"    # written by the tray
NOTIF_STATE_FILE = CLAUSAGE_DIR / "notif_state.json"
LOG_FILE = CLAUSAGE_DIR / "clausage.log"
RAW_DIR = CLAUSAGE_DIR / "raw"

# Files this app must never read. remote-settings.json carries an org OTLP
# bearer token; nothing here needs it.
DENY_READ = frozenset({
    (CLAUDE_DIR / "remote-settings.json").resolve().as_posix().lower(),
})


def assert_readable(path: Path) -> None:
    """Guard against ever reading a deny-listed file."""
    try:
        key = Path(path).resolve().as_posix().lower()
    except OSError:
        return
    if key in DENY_READ:
        raise PermissionError(f"refusing to read deny-listed file: {path}")


# ---------------------------------------------------------------- endpoint

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
ANTHROPIC_BETA = "oauth-2025-04-20"
FALLBACK_CC_VERSION = "2.1.263"
HTTP_TIMEOUT_S = 20.0

# ---------------------------------------------------------------- timing

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


POLL_S = _env_int("CLAUSAGE_POLL_S", 180)        # documented-safe cadence
IDLE_POLL_S = _env_int("CLAUSAGE_IDLE_POLL_S", 900)
IDLE_AFTER_S = _env_int("CLAUSAGE_IDLE_AFTER_S", 600)
TICK_S = _env_int("CLAUSAGE_TICK_S", 5)          # UI re-evaluation cadence

BACKOFF_LADDER_S = (180, 360, 720, 1800)
REFRESH_COOLDOWN_S = 300
TOKEN_SKEW_S = 120                                # refresh this long before expiry

FRESH_S = _env_int("CLAUSAGE_FRESH_S", 360)       # 6 min
DEAD_S = _env_int("CLAUSAGE_DEAD_S", 12 * 3600)
API_PREFERRED_S = 360                             # prefer API while this fresh
COLD_START_MAX_AGE_S = 900                        # don't toast off stale data
DISAGREEMENT_POINTS = 3.0

# ---------------------------------------------------------------- display

THRESHOLDS = (50, 75, 100)

# The usage-credits pool, tracked alongside the two rate-limit windows. It is
# the only number here denominated in real money, so it gets the same 50/75/100
# alerts -- previously it was displayed but never announced, and could reach the
# cap silently.
CREDITS_WINDOW = "credits"

# Credits carry no resets_at, so a reset is inferred. A fall of this many
# points in the used percentage is read as "the pool rolled over" (or the cap
# was raised) and re-arms the thresholds. Well above rounding noise, and a
# genuine monthly reset drops to ~0.
CREDITS_RESET_DROP = 10.0

# Declare per-monitor DPI awareness at startup. On a display at 150% scaling
# this is the difference between a 24px badge and a 16px one stretched to 24.
# Set CLAUSAGE_DPI_AWARE=0 to go back to letting Windows scale it.
DPI_AWARE = os.environ.get("CLAUSAGE_DPI_AWARE", "1").strip() not in ("0", "false", "no")

# A multiplier applied on top of the monitor's own scaling, for the panel only.
#
# The panel already follows Windows display scaling, which is usually what you
# want -- but "usually" is not "always". A 1080p laptop left at 100% where
# Windows would have recommended 125% gets a panel a third smaller than the
# same build on a 150% screen, and the only fix was to change the display
# scaling for *every* application on the machine. This changes one window.
#
# Clamped rather than trusted: a typo of 10 instead of 1.0 would otherwise
# produce a window larger than the desktop, with its close control off-screen
# and no title bar to drag it back by.
PANEL_SCALE_MIN, PANEL_SCALE_MAX = 0.5, 3.0


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


PANEL_SCALE = min(PANEL_SCALE_MAX,
                  max(PANEL_SCALE_MIN,
                      _env_float("CLAUSAGE_PANEL_SCALE", 1.0)))

# Show the rendered hover card instead of the native text tooltip.
#
# Off by default: hover is meant to be a glance, and Windows' own tooltip is
# what a glance expects -- it appears where the eye already is, needs no
# window, and cannot be caught behind anything. The card is still here and
# still works (CLAUSAGE_HOVER_CARD=1); it turned out to be the wrong surface
# for the job rather than a bad implementation of it.
HOVER_CARD = os.environ.get("CLAUSAGE_HOVER_CARD", "0").strip()     in ("1", "true", "yes")

# These are band fills behind white digits, so they are a touch deeper than a
# text palette would be -- white needs contrast to stay crisp at 16px.
COLORS = {
    "ok": (46, 170, 74),
    "warn": (208, 145, 10),
    "hot": (208, 52, 48),
    "unknown": (95, 102, 112),
}
STALE_BLEND = (118, 125, 135)
TILE_COLOR = (24, 26, 31)
SEAM_COLOR = (18, 20, 24)

# Pale tints for the "light" badge: a bright tile stands out hard against a
# dark taskbar, and dark ink on it is the highest-contrast pairing available.
# Measured against INK_DARK these run about 14-15:1, far past the WCAG AA
# threshold of 4.5:1 for small text.
PALE_COLORS = {
    "ok": (206, 243, 216),
    "warn": (253, 237, 194),
    "hot": (253, 214, 211),
    "unknown": (222, 226, 232),
}
INK_DARK = (16, 18, 22)
INK_STALE = (108, 114, 124)

USAGE_PAGE_URL = "https://claude.ai/settings/usage"
APP_ID = "clausage_bar"
# The single-instance guard. Suffixed via the environment so a second copy can
# be run deliberately -- which is what verifying a standalone build against a
# machine that already has the tray running requires. Without this the only
# way to test a new build was to kill the working one first, and "kill the
# thing that works, then check the new thing" is a bad verification story.
_INSTANCE = os.environ.get("CLAUSAGE_INSTANCE", "").strip()
MUTEX_NAME = r"Local\clausage_bar" + ("-" + _INSTANCE if _INSTANCE else "")
TOOLTIP_MAX = 127                                 # NOTIFYICONDATA.szTip is 128

# Show the big floating indicator as soon as the tray starts. The tray icon is
# capped at 16px by Windows; this is the surface that is not.
SHOW_WIDGET = os.environ.get("CLAUSAGE_SHOW_WIDGET", "").strip() in ("1", "true", "yes")

# "auto" walks the refresh ladder, escalating until the token actually
# rotates. Naming a single step pins it; "none" disables refresh entirely.
REFRESH_COMMAND = os.environ.get(
    "CLAUSAGE_REFRESH_COMMAND", "auto"
)  # "auto" | "auth-status" | "update" | "none"


def ensure_dirs() -> None:
    CLAUSAGE_DIR.mkdir(parents=True, exist_ok=True)
