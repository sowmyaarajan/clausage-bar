#!/usr/bin/env node
// ──────────────────────────────────────────────────────────────────
//  Claude Code status line
//  Shows: model | ctx bar | 5h session bar | weekly bar
//
//  The 5h and weekly bars read the real rate_limits Claude Code supplies on
//  stdin. They used to be approximations: the 5h bar measured elapsed
//  wall-clock time rather than usage, and the weekly bar compared peak context
//  size against a hardcoded 1M token guess. Neither reflected actual quota, so
//  both were replaced. When rate_limits is absent we print "usage:n/a" rather
//  than invent a number.
//
//  Original preserved at statusline.js.clausage-bak
// ──────────────────────────────────────────────────────────────────

const chunks = [];
process.stdin.on('data', d => chunks.push(d));
process.stdin.on('end', () => {
  try {
    const data = JSON.parse(chunks.join(''));

    // clausage_bar collector (fail-open; must never break the statusline)
    try { require('./clausage_collector.js').capture(data); } catch (_) {}

    const model      = (data.model && (data.model.display_name || data.model.id)) || '';
    const ctxPct     = data.context_window && data.context_window.used_percentage;

    // Shorten model: "claude-sonnet-4-6[1m]" → "Sonnet"
    const short = model
      .replace(/claude-/i, '')
      .replace(/[-\[].*/, '')
      .replace(/^(\w)/, c => c.toUpperCase());

    // ── ANSI colours ──────────────────────────────────────────────
    const G = '\x1b[32m';   // green  (< 50 %)
    const Y = '\x1b[33m';   // orange (50–75 %)
    const R = '\x1b[31m';   // red    (> 75 %)
    const D = '\x1b[90m';   // dim grey (empty blocks)
    const W = '\x1b[97m';   // white
    const X = '\x1b[0m';    // reset

    function pickColor(p) {
      if (p < 50) return G;
      if (p < 75) return Y;
      return R;
    }

    function makeBar(p, width = 10) {
      const filled = Math.min(width, Math.round(p / (100 / width)));
      return pickColor(p) + '█'.repeat(filled) + D + '░'.repeat(width - filled) + X;
    }

    // ── rate limits ───────────────────────────────────────────────
    const PCT_KEYS   = ['used_percentage', 'usedPercentage', 'utilization', 'percent'];
    const RESET_KEYS = ['resets_at', 'resetsAt', 'reset_at', 'resetAt'];

    function firstKey(obj, keys) {
      if (!obj || typeof obj !== 'object') return undefined;
      for (const k of keys) if (obj[k] !== undefined && obj[k] !== null) return obj[k];
      return undefined;
    }

    const rl = data.rate_limits || data.rateLimits || {};

    function readWindow(names) {
      for (const name of names) {
        const node = rl[name];
        if (node && typeof node === 'object') {
          const pct = firstKey(node, PCT_KEYS);
          if (typeof pct === 'number') {
            return { pct: pct, resets: firstKey(node, RESET_KEYS) };
          }
        }
      }
      return null;
    }

    const fiveHour = readWindow(['five_hour', 'fiveHour', 'session']);
    const sevenDay = readWindow(['seven_day', 'sevenDay', 'weekly', 'weekly_all']);

    // Time remaining until a reset, e.g. "3h05m" or "6d".
    // resets_at arrives as a Unix epoch in SECONDS here (the HTTP usage
    // endpoint sends ISO-8601 instead), so scale it before Date sees it --
    // new Date(1788787800) would read that as milliseconds and land in 1970.
    function toMillis(resets) {
      if (typeof resets === 'number') {
        return resets > 1e11 ? resets : resets * 1000;
      }
      if (typeof resets === 'string' && /^\d+$/.test(resets)) {
        return toMillis(Number(resets));
      }
      return new Date(resets).getTime();
    }

    function timeLeft(resets) {
      if (!resets) return null;
      const target = toMillis(resets);
      if (isNaN(target)) return null;
      const remaining = target - Date.now();
      if (remaining <= 0) return 'due';
      const mins = Math.floor(remaining / 60000);
      if (mins < 60) return mins + 'm';
      const hrs = Math.floor(mins / 60);
      if (hrs < 48) return hrs + 'h' + String(mins % 60).padStart(2, '0') + 'm';
      return Math.floor(hrs / 24) + 'd';
    }

    function segment(label, window, width) {
      if (!window) return null;
      let out = '  ' + label + ':' + makeBar(window.pct, width)
              + ' ' + pickColor(window.pct) + Math.round(window.pct) + '%' + X;
      const left = timeLeft(window.resets);
      if (left) out += D + ' ' + left + X;
      return out;
    }

    // ── Assemble output ───────────────────────────────────────────
    let out = W + '[' + short + ']' + X;

    if (ctxPct !== undefined && ctxPct !== null) {
      out += '  ctx:' + makeBar(ctxPct, 10)
           + ' ' + pickColor(ctxPct) + Math.round(ctxPct) + '%' + X;
    }

    const fiveSeg = segment('5h', fiveHour, 5);
    const weekSeg = segment('week', sevenDay, 5);

    if (fiveSeg || weekSeg) {
      if (fiveSeg) out += fiveSeg;
      if (weekSeg) out += weekSeg;
    } else {
      // No rate limits in the payload. Say so rather than guess.
      out += D + '  usage:n/a' + X;
    }

    process.stdout.write(out);
  } catch {
    process.stdout.write('');
  }
});
