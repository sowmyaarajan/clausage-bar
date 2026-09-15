"""One-shot probe of the undocumented OAuth usage endpoint.

Prints the parsed response with the token redacted, so we can confirm the real
field names before building anything on top of them.

    python tools/probe_endpoint.py [--raw] [--no-ua]

--no-ua deliberately omits the User-Agent to demonstrate the 429 bucket.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests  # noqa: E402

from clausage_bar import auth, config  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", action="store_true", help="dump the raw JSON body")
    ap.add_argument("--no-ua", action="store_true",
                    help="omit User-Agent (expected to 429)")
    args = ap.parse_args()

    creds = auth.load()
    if creds is None:
        print("FAIL: could not load credentials from", config.CREDENTIALS_FILE)
        return 2

    print("credentials:", json.dumps(creds.redacted(), indent=2))
    if creds.signed_out():
        print("FAIL: refresh token expired -- run: claude auth login")
        return 2
    if creds.expired():
        print("NOTE: access token is expired/near expiry; requesting a refresh...")
        ok = auth.RefreshTrigger().trigger()
        print("      refresh command exited cleanly:", ok)
        refreshed = auth.load()
        if refreshed:
            print("      after refresh:", json.dumps(refreshed.redacted(), indent=2))
            changed = refreshed.access_token != creds.access_token
            print("      >>> token actually changed:", changed)
            creds = refreshed

    version = auth.cli_version()
    headers = {
        "Authorization": f"Bearer {creds.access_token}",
        "anthropic-beta": config.ANTHROPIC_BETA,
        "Content-Type": "application/json",
    }
    if not args.no_ua:
        headers["User-Agent"] = f"claude-code/{version}"

    print(f"\nGET {config.USAGE_URL}")
    print("headers:", json.dumps(
        {k: ("Bearer <redacted>" if k == "Authorization" else v)
         for k, v in headers.items()}, indent=2))

    try:
        resp = requests.get(config.USAGE_URL, headers=headers,
                            timeout=config.HTTP_TIMEOUT_S)
    except requests.RequestException as exc:
        print("FAIL: request error:", exc)
        return 2

    print("\nHTTP", resp.status_code)
    for key in ("retry-after", "anthropic-ratelimit-unified-reset",
                "x-should-retry", "request-id"):
        if key in resp.headers:
            print(f"  {key}: {resp.headers[key]}")

    if resp.status_code != 200:
        print("body:", resp.text[:1000])
        if resp.status_code == 429:
            print("\n>>> 429. If --no-ua was NOT used, the User-Agent format may be wrong.")
        return 1

    try:
        body = resp.json()
    except ValueError:
        print("FAIL: response was not JSON:", resp.text[:500])
        return 1

    if args.raw:
        print("\nraw body:\n", json.dumps(body, indent=2))

    print("\ntop-level keys:", sorted(body) if isinstance(body, dict) else type(body))

    now = datetime.now(timezone.utc)
    print("\n--- parsed windows ---")
    for name in ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet"):
        node = body.get(name) if isinstance(body, dict) else None
        if node is None:
            print(f"  {name:18s} null")
            continue
        print(f"  {name:18s} keys={sorted(node) if isinstance(node, dict) else node}")
        if isinstance(node, dict):
            pct = node.get("utilization", node.get("used_percentage"))
            resets = node.get("resets_at")
            note = ""
            if isinstance(resets, str):
                try:
                    dt = datetime.fromisoformat(resets.replace("Z", "+00:00"))
                    delta = dt - now
                    mins = int(delta.total_seconds() // 60)
                    note = (f"  -> local {dt.astimezone():%Y-%m-%d %H:%M} "
                            f"({mins//60}h{abs(mins)%60:02d}m away)")
                except ValueError:
                    note = "  -> UNPARSEABLE"
            print(f"  {'':18s} utilization={pct!r} resets_at={resets!r}{note}")

    extra = body.get("extra_usage") if isinstance(body, dict) else None
    print("\n--- extra_usage ---")
    print(" ", json.dumps(extra, indent=2) if extra is not None else "null")

    unknown = (set(body) - {"five_hour", "seven_day", "seven_day_opus",
                            "seven_day_sonnet", "extra_usage"}
               ) if isinstance(body, dict) else set()
    if unknown:
        print("\nNOTE: undocumented extra keys present:", sorted(unknown))

    print("\nGATE: session(5h) =", (body.get("five_hour") or {}).get("utilization"),
          "% | weekly(7d) =", (body.get("seven_day") or {}).get("utilization"), "%")
    print("Cross-check these against /usage inside Claude Code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
