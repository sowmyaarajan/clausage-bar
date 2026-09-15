# The status-line changes, for manual or audited application

`install.ps1` does both of these for you. This file is the record of what it
changes, so you can apply or review them by hand.

The original is backed up to `statusline.js.clausage-bak` and restored by
`uninstall.ps1`.

---

## 1. The collector tap (required for the fallback source)

Insert immediately after the payload is parsed:

```js
    const data = JSON.parse(chunks.join(''));

    // clausage_bar collector (fail-open; must never break the statusline)
    try { require('./clausage_collector.js').capture(data); } catch (_) {}
```

`require` resolves relative to `statusline.js`, so it finds
`~/.claude/clausage_collector.js`. The `try/catch` means a missing or broken
collector is swallowed and the status line renders exactly as before.

This is deliberately a patch rather than a wrapper process. The status line is
respawned on every render; a wrapper would add ~50-90 ms of Node startup to
that hot path, plus four failure modes around single-consumption stdin (EPIPE,
spawn failure, partial write, exit codes), each able to blank the status line.
The patch costs 1-3 ms and backs out by deleting two lines.

## 2. Real numbers in the 5h and weekly bars (optional but recommended)

The original bars did not show usage:

| Original | Problem |
|---|---|
| 5h bar = elapsed wall-clock time since `.session_start` | measured time passing, not usage |
| weekly bar vs `WEEKLY_LIMIT = 1_000_000` | a hardcoded guess; unified limits are not token budgets |
| `.weekly_usage.json` accumulated **peak context size** per session | not cumulative usage, and not a quota reading |

`collector/statusline.reference.js` is the rewritten version: it reads
`rate_limits.{five_hour,seven_day}` from the payload Claude Code already
supplies, keeps the original layout, colors, bar glyphs and model shortening,
and prints `usage:n/a` when `rate_limits` is absent rather than inventing a
number.

To apply it wholesale:

```powershell
Copy-Item ~\.claude\statusline.js ~\.claude\statusline.js.clausage-bak
Copy-Item .\collector\statusline.reference.js ~\.claude\statusline.js
```

`.session_start` and `.weekly_usage.json` become unused and can be deleted.

### Two format traps this version handles

`resets_at` arrives from the status line as a **Unix epoch in seconds**
(`1788787800`), while the HTTP usage endpoint sends **ISO-8601**
(`2026-09-07T13:30:00.798800+00:00`) for the same instant. `new Date(1788787800)`
reads that number as milliseconds and lands in 1970, which makes every window
look overdue. `toMillis()` scales by magnitude before `Date` sees it.

The percentage can arrive as `used_percentage` (status line) or `utilization`
(endpoint), so both are probed.

## Smoke test after any change

All three must exit 0, and the last must still print something:

```bash
echo '{"model":{"display_name":"T"},"rate_limits":{"five_hour":{"used_percentage":62,"resets_at":1788787800}}}' | node ~/.claude/statusline.js
echo '{"model":{"display_name":"T"}}' | node ~/.claude/statusline.js   # -> usage:n/a
echo 'garbage' | node ~/.claude/statusline.js                          # -> empty, exit 0
```
