"""Source precedence and cross-checking.

Precedence:
  1. An API reading younger than API_PREFERRED_S.
  2. Otherwise whichever source is newer.
  3. Otherwise the cached snapshot from a previous run.

When both sources are fresh they are compared. A disagreement is the only
signal we have that the undocumented endpoint has drifted, and it costs
nothing to watch for.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import config
from .logging_setup import get
from .model import PRIMARY_WINDOWS, Snapshot

log = get("merge")

_warned: set[str] = set()


def cross_check(api: Snapshot | None, statusline: Snapshot | None,
                now: datetime | None = None) -> None:
    if api is None or statusline is None:
        return
    now = now or datetime.now(timezone.utc)
    if api.age_s(now) > config.FRESH_S or statusline.age_s(now) > config.FRESH_S:
        return
    for name in PRIMARY_WINDOWS:
        left, right = api.window(name), statusline.window(name)
        if left is None or right is None:
            continue
        delta = abs(left.utilization - right.utilization)
        if delta > config.DISAGREEMENT_POINTS and name not in _warned:
            _warned.add(name)
            log.warning("%s disagreement: api=%.1f%% statusline=%.1f%% (%.1f points). "
                        "The endpoint or the status line contract may have changed.",
                        name, left.utilization, right.utilization, delta)


def choose(api: Snapshot | None, statusline: Snapshot | None,
           cached: Snapshot | None, now: datetime | None = None) -> Snapshot | None:
    """Pick the snapshot to display, annotating which sources were seen."""
    now = now or datetime.now(timezone.utc)
    cross_check(api, statusline, now)

    available = [s for s in (api, statusline) if s is not None]
    seen = sorted({s.source for s in available})

    chosen: Snapshot | None = None
    if api is not None and api.age_s(now) <= config.API_PREFERRED_S:
        chosen = api
    elif available:
        chosen = max(available, key=lambda s: s.captured_at)

    if chosen is None:
        if cached is None:
            return None
        cached.sources = list(cached.sources or [cached.source])
        return cached

    if seen:
        chosen.sources = seen
    return chosen
