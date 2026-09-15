"""Reads state.json, written by the Node status-line collector.

Free but session-bound: it only advances while a Claude Code session is open.
Used as the fallback when the HTTP endpoint is unavailable, and as a no-cost
cross-check on it when both are fresh.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import config
from .atomicjson import read_json, stat_key
from .logging_setup import get
from .model import Snapshot, Spend, WindowUsage, _parse_dt

log = get("statusline")


class StatuslineProvider:
    def __init__(self, path=None) -> None:
        self.path = path or config.STATE_FILE
        self._stat: tuple[int, int] | None = None
        self._cached: Snapshot | None = None
        self._probe_logged = False

    def changed(self) -> bool:
        return stat_key(self.path) != self._stat

    def read(self) -> Snapshot | None:
        """Re-parse only when the file actually changed."""
        current = stat_key(self.path)
        if current == self._stat and self._cached is not None:
            return self._cached
        raw = read_json(self.path)
        if raw is None:
            # Absent, or a torn read: keep whatever we had.
            return self._cached
        snapshot = self._parse(raw)
        if snapshot is not None:
            self._stat = current
            self._cached = snapshot
        return self._cached

    def _parse(self, raw: Any) -> Snapshot | None:
        from . import SCHEMA_VERSION

        if not isinstance(raw, dict):
            return None
        if raw.get("schema") != SCHEMA_VERSION:
            log.warning("state.json schema %s not understood (expected %s)",
                        raw.get("schema"), SCHEMA_VERSION)
            return None

        captured = _parse_dt(raw.get("captured_at"))
        if captured is None:
            epoch = raw.get("captured_at_epoch_ms")
            if isinstance(epoch, (int, float)):
                captured = datetime.fromtimestamp(epoch / 1000.0, tz=timezone.utc)
        if captured is None:
            return None

        raw_windows = raw.get("windows")
        if not isinstance(raw_windows, dict):
            return None

        windows: dict[str, WindowUsage | None] = {}
        for name, node in raw_windows.items():
            if name == "spend_limit":
                continue
            windows[name] = WindowUsage.from_payload(node, key_path=(
                node.get("raw_key_path") if isinstance(node, dict) else None))

        if windows.get("five_hour") is None and windows.get("seven_day") is None:
            self._log_probe(raw)
            return None

        spend = Spend.from_payload(None, raw_windows.get("spend_limit"))
        return Snapshot(
            captured_at=captured,
            source="statusline",
            windows=windows,
            spend=spend,
            model_name=raw.get("model"),
            sources=["statusline"],
        )

    def _log_probe(self, raw: Any) -> None:
        """Explain, once, why we could not find rate limits in the payload."""
        if self._probe_logged:
            return
        self._probe_logged = True
        probe = raw.get("probe") if isinstance(raw, dict) else None
        if not isinstance(probe, dict):
            log.warning("state.json has no usable windows and no probe block")
            return
        log.warning("status line payload carried no recognizable rate limits. "
                    "top-level keys=%s fallback_hits=%s",
                    probe.get("top_level_keys"), probe.get("fallback_hits"))
