"""Age classification and reset inference.

The failure mode that would destroy trust in this app is a red 92% badge that
has been stale for six hours while the window has actually rolled over. Two
mechanisms guard against it:

*Age classes* decide how much to trust the snapshot at all.

*Reset inference* uses ``resets_at``: if that moment has passed, the window
genuinely is at 0% regardless of how old our reading is. ``resets_at`` is
authoritative about *when*, so this is a sound inference rather than a guess.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import config
from .model import WINDOW_LENGTHS, Eff, Snapshot, WindowUsage

FRESH = "fresh"
STALE = "stale"
UNKNOWN = "unknown"


def health(age_s: float) -> str:
    if age_s > config.DEAD_S:
        return UNKNOWN
    if age_s > config.FRESH_S:
        return STALE
    return FRESH


def fmt_ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "{0}s".format(seconds)
    minutes = seconds // 60
    if minutes < 60:
        return "{0}m".format(minutes)
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return "{0}h{1:02d}m".format(hours, minutes)
    return "{0}d".format(hours // 24)


def fmt_until(target: datetime | None, now: datetime | None = None) -> str | None:
    """Short 'time remaining' string, or None when unknown/past."""
    if target is None:
        return None
    now = now or datetime.now(timezone.utc)
    delta = (target - now).total_seconds()
    if delta <= 0:
        return None
    return fmt_ago(delta)


def effective(window: WindowUsage | None, name: str,
              now: datetime | None = None) -> Eff:
    """Resolve a window to what is actually true right now."""
    now = now or datetime.now(timezone.utc)

    if window is None:
        return Eff(pct=None, note="no data")

    resets_at = window.resets_at
    if resets_at is not None and now >= resets_at:
        # The window rolled over. Project forward to the next boundary so
        # threshold state re-arms against a stable key.
        length = WINDOW_LENGTHS.get(name, timedelta(hours=5))
        next_reset = resets_at
        # Guard the loop: a very old reading could be many windows back.
        for _ in range(1000):
            if next_reset > now:
                break
            next_reset = next_reset + length
        return Eff(
            pct=0.0,
            next_reset=next_reset,
            inferred=True,
            note="window reset {0} ago, assumed".format(fmt_ago((now - resets_at).total_seconds())),
        )

    return Eff(pct=window.utilization, next_reset=resets_at, inferred=False)


def evaluate(snapshot: Snapshot | None,
             now: datetime | None = None) -> tuple[dict[str, Eff], str, float]:
    """Resolve every window in a snapshot.

    Returns ``(effs, health, age_seconds)``.
    """
    now = now or datetime.now(timezone.utc)
    if snapshot is None:
        return {}, UNKNOWN, float("inf")

    age = snapshot.age_s(now)
    effs = {name: effective(window, name, now)
            for name, window in snapshot.windows.items()}
    return effs, health(age), age
