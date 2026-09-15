"""Pipe synthetic status-line payloads through the real Node collector.

Lets the whole status-line path be developed and tested without waiting for a
live Claude Code session.

    python tools/fake_payload.py                 # the expected shape
    python tools/fake_payload.py --case no-limits
    python tools/fake_payload.py --case all      # run every case
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "collector" / "clausage_collector.js"

now = datetime.now(timezone.utc)


def iso(delta: timedelta) -> str:
    return (now + delta).isoformat()


BASE = {
    "model": {"id": "claude-opus-5[1m]", "display_name": "Opus 5"},
    "context_window": {"used_tokens": 51234, "used_percentage": 25.6},
    "effort": {"level": "medium"},
    "session_id": "00000000-0000-0000-0000-000000000000",
    "workspace": {"added_dirs": [], "git_worktree": None},
}

CASES: dict[str, dict] = {
    # What the changelog describes.
    "expected": {
        **BASE,
        "rate_limits": {
            "five_hour": {"used_percentage": 41.0, "resets_at": iso(timedelta(hours=3))},
            "seven_day": {"used_percentage": 64.0, "resets_at": iso(timedelta(days=3))},
            "spend_limit": {"used_percentage": 68.0, "resets_at": None},
        },
    },
    # Same data under the endpoint's naming, to prove the probe copes.
    "utilization-naming": {
        **BASE,
        "rate_limits": {
            "five_hour": {"utilization": 41.0, "resets_at": iso(timedelta(hours=3))},
            "seven_day": {"utilization": 64.0, "resets_at": iso(timedelta(days=3))},
        },
    },
    # camelCase drift.
    "camel-naming": {
        **BASE,
        "rateLimits": {
            "five_hour": {"usedPercentage": 41.0, "resetsAt": iso(timedelta(hours=3))},
            "seven_day": {"usedPercentage": 64.0, "resetsAt": iso(timedelta(days=3))},
        },
    },
    # A window that has already rolled over.
    "rolled-over": {
        **BASE,
        "rate_limits": {
            "five_hour": {"used_percentage": 92.0, "resets_at": iso(timedelta(hours=-2))},
            "seven_day": {"used_percentage": 64.0, "resets_at": iso(timedelta(days=3))},
        },
    },
    # Percentages high enough to cross every threshold at once.
    "high": {
        **BASE,
        "rate_limits": {
            "five_hour": {"used_percentage": 99.5, "resets_at": iso(timedelta(hours=1))},
            "seven_day": {"used_percentage": 88.0, "resets_at": iso(timedelta(days=1))},
        },
    },
    # The degraded case: no rate limits at all. Expect the probe to say so.
    "no-limits": {**BASE},
    # Hostile input: the collector must not throw.
    "garbage": {"rate_limits": "not-an-object", "model": None},
}


def run(name: str, payload: dict) -> int:
    print("=" * 68)
    print("case:", name)
    proc = subprocess.run(["node", str(COLLECTOR)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30)
    print("  node exit:", proc.returncode, proc.stdout.strip() or proc.stderr.strip())
    if proc.returncode != 0:
        return 1

    sys.path.insert(0, str(ROOT / "src"))
    from clausage_bar import config
    from clausage_bar.atomicjson import read_json
    from clausage_bar.provider_statusline import StatuslineProvider

    state = read_json(config.STATE_FILE)
    probe = (state or {}).get("probe", {})
    print("  rate_limits_present:", probe.get("rate_limits_present"))
    if not probe.get("rate_limits_present"):
        print("  top_level_keys:", probe.get("top_level_keys"))
        print("  fallback_hits:", probe.get("fallback_hits"))

    snapshot = StatuslineProvider().read()
    if snapshot is None:
        print("  parsed -> None (no usable windows)")
        return 0
    for window in ("five_hour", "seven_day"):
        parsed = snapshot.windows.get(window)
        print("  {0:12s} {1}".format(
            window,
            "{0:.1f}%  resets {1}  via {2}".format(
                parsed.utilization, parsed.resets_at, parsed.raw_key_path)
            if parsed else "(none)"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="expected",
                    choices=sorted(CASES) + ["all"])
    args = ap.parse_args()

    names = sorted(CASES) if args.case == "all" else [args.case]
    failures = 0
    for name in names:
        failures += run(name, CASES[name])
    print("=" * 68)
    print("done;", failures, "failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
