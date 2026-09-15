"""Inspect what the status line actually handed us.

Prints the captured payload's key tree, which candidate key paths resolved,
and the parsed result. Use this when the tray says it has no status-line data.

    python tools/dump_probe.py [--depth 3] [--full]
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from clausage_bar import config, freshness  # noqa: E402
from clausage_bar.atomicjson import read_json  # noqa: E402
from clausage_bar.provider_statusline import StatuslineProvider  # noqa: E402

REDACT_KEYS = {"transcript_path", "cwd", "scratchpad_dir", "session_id",
               "prompt_id", "session_name"}


def tree(node, depth: int, indent: int = 2, redact: bool = True) -> None:
    pad = " " * indent
    if isinstance(node, dict):
        for key, value in node.items():
            if redact and key in REDACT_KEYS:
                print("{0}{1}: <redacted>".format(pad, key))
                continue
            if isinstance(value, (dict, list)) and depth > 1:
                print("{0}{1}:".format(pad, key))
                tree(value, depth - 1, indent + 2, redact)
            else:
                summary = value
                if isinstance(value, dict):
                    summary = "{{...{0} keys}}".format(len(value))
                elif isinstance(value, list):
                    summary = "[...{0} items]".format(len(value))
                elif isinstance(value, str) and len(value) > 60:
                    summary = value[:57] + "..."
                print("{0}{1}: {2!r}".format(pad, key, summary))
    elif isinstance(node, list):
        for i, value in enumerate(node[:5]):
            print("{0}[{1}]:".format(pad, i))
            tree(value, depth - 1, indent + 2, redact)
        if len(node) > 5:
            print("{0}... {1} more".format(pad, len(node) - 5))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--full", action="store_true",
                    help="do not redact paths and identifiers")
    args = ap.parse_args()

    print("state file:", config.STATE_FILE)
    state = read_json(config.STATE_FILE)
    if state is None:
        print("\nNo state.json. That means the status-line collector has not run.")
        print("Check that:")
        print("  1. ~/.claude/clausage_collector.js exists")
        print("  2. ~/.claude/statusline.js contains a require of it")
        print("  3. settings.json points statusLine.command at statusline.js")
        print("  4. a Claude Code session has rendered its status line since")
        return 1

    probe = state.get("probe", {})
    print("collector version:", state.get("collector_version"))
    print("captured at:      ", state.get("captured_at"))
    print("rate_limits found:", probe.get("rate_limits_present"))
    print()

    print("--- resolved windows ---")
    for name, node in (state.get("windows") or {}).items():
        if node:
            print("  {0:18s} {1:>6.1f}%   resets_at={2!r}   via {3}".format(
                name, node.get("utilization", float("nan")),
                node.get("resets_at"), node.get("raw_key_path")))
        else:
            print("  {0:18s} (not found)".format(name))

    if not probe.get("rate_limits_present"):
        print()
        print("--- diagnosis ---")
        print("  payload top-level keys:", probe.get("top_level_keys"))
        hits = probe.get("fallback_hits") or []
        if hits:
            print("  generic scan found percentage-like fields:")
            for hit in hits:
                print("    {0} = {1}  (reset: {2})".format(
                    hit.get("path"), hit.get("value"), hit.get("reset")))
            print("  If one of these is a rate limit, add its path to WINDOWS")
            print("  in collector/clausage_collector.js.")
        else:
            print("  no percentage-like fields anywhere in the payload.")
            print("  This Claude Code build may not expose rate_limits to the")
            print("  status line. The HTTP endpoint remains the primary source.")

    print()
    print("--- parsed snapshot ---")
    snapshot = StatuslineProvider().read()
    if snapshot is None:
        print("  parsed to None (no usable windows)")
    else:
        print("  age:", freshness.fmt_ago(snapshot.age_s()))
        for name in ("five_hour", "seven_day"):
            window = snapshot.windows.get(name)
            print("  {0:12s} {1}".format(
                name,
                "{0:.1f}%  resets {1}".format(window.utilization, window.resets_at)
                if window else "(none)"))

    dumps = sorted(glob.glob(str(config.RAW_DIR / "payload-*.json")))
    print()
    print("--- raw payload dumps ({0}) ---".format(len(dumps)))
    if not dumps:
        print("  none. Delete", config.RAW_DIR, "to capture a fresh set.")
        return 0
    latest = dumps[-1]
    print("  newest:", latest)
    payload = read_json(Path(latest))
    print()
    tree(payload, args.depth, redact=not args.full)

    rl = (payload or {}).get("rate_limits")
    if rl is not None:
        print()
        print("--- rate_limits verbatim ---")
        print(json.dumps(rl, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
