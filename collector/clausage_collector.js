// ──────────────────────────────────────────────────────────────────
//  clausage_bar collector
//
//  Taps the JSON payload Claude Code pipes to the status line and records
//  rate_limits to ~/.claude/clausage/state.json. This is the free fallback
//  source: it costs no API call, but only advances while a session is open.
//
//  Contract: capture() MUST NEVER THROW. The status line has to render even
//  if everything in here is broken.
// ──────────────────────────────────────────────────────────────────
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const VERSION = '0.1.0';
const SCHEMA = 1;

const CLAUDE_DIR = process.env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), '.claude');
const DIR = path.join(CLAUDE_DIR, 'clausage');
const STATE = path.join(DIR, 'state.json');
const RAW = path.join(DIR, 'raw');
const MAX_RAW_DUMPS = 3;

// Candidate key paths, most likely first. The sub-key naming for the status
// line payload is less certain than the HTTP endpoint's, so we probe.
const WINDOWS = {
  five_hour: [
    ['rate_limits', 'five_hour'], ['rate_limits', 'fiveHour'], ['rate_limits', 'session'],
    ['rateLimits', 'five_hour'], ['rate_limit', 'five_hour'],
  ],
  seven_day: [
    ['rate_limits', 'seven_day'], ['rate_limits', 'sevenDay'], ['rate_limits', 'weekly'],
    ['rate_limits', 'weekly_all'], ['rateLimits', 'seven_day'],
  ],
  seven_day_opus: [['rate_limits', 'seven_day_opus'], ['rate_limits', 'weekly_opus']],
  seven_day_sonnet: [['rate_limits', 'seven_day_sonnet'], ['rate_limits', 'weekly_sonnet']],
  spend_limit: [['rate_limits', 'spend_limit'], ['rate_limits', 'spendLimit']],
};

const PCT_KEYS = ['used_percentage', 'usedPercentage', 'utilization', 'percent',
                  'used_pct', 'percent_used'];
const RESET_KEYS = ['resets_at', 'resetsAt', 'reset_at', 'resetAt'];

function dig(obj, keyPath) {
  return keyPath.reduce(
    (acc, key) => (acc && typeof acc === 'object' ? acc[key] : undefined), obj);
}

function firstKey(obj, keys) {
  if (!obj || typeof obj !== 'object') return undefined;
  for (const key of keys) {
    if (obj[key] !== undefined && obj[key] !== null) return obj[key];
  }
  return undefined;
}

function readWindow(data, candidates) {
  for (const keyPath of candidates) {
    const node = dig(data, keyPath);
    if (node && typeof node === 'object') {
      const pct = firstKey(node, PCT_KEYS);
      if (typeof pct === 'number') {
        const resets = firstKey(node, RESET_KEYS);
        return {
          utilization: pct,
          resets_at: resets === undefined ? null : resets,
          raw_key_path: keyPath.join('.'),
        };
      }
    }
  }
  return null;
}

// Generic sweep for any {*percent*|*utili*} number beside a *reset* sibling.
// Only runs when the named paths all miss, and only to help diagnosis.
function scanFallback(root) {
  const hits = [];
  const seen = new Set();
  (function walk(node, trail, depth) {
    if (!node || typeof node !== 'object' || depth > 4 || seen.has(node)) return;
    seen.add(node);
    for (const key of Object.keys(node)) {
      if (/used.?perc|percent|utili/i.test(key) && typeof node[key] === 'number') {
        hits.push({
          path: trail.concat(key).join('.'),
          value: node[key],
          reset: firstKey(node, RESET_KEYS) || null,
        });
      }
    }
    for (const key of Object.keys(node)) walk(node[key], trail.concat(key), depth + 1);
  })(root, [], 0);
  return hits;
}

function writeAtomic(file, text) {
  const tmp = file + '.' + process.pid + '.tmp';
  for (let i = 0; i < 3; i++) {
    try {
      fs.writeFileSync(tmp, text);
      fs.renameSync(tmp, file);   // MoveFileEx: readers never see a torn file
      return true;
    } catch (_) {
      try { fs.unlinkSync(tmp); } catch (_) { /* ignore */ }
    }
  }
  return false;
}

function dumpRawOnce(data) {
  try {
    fs.mkdirSync(RAW, { recursive: true });
    if (fs.readdirSync(RAW).length >= MAX_RAW_DUMPS) return;
    writeAtomic(path.join(RAW, 'payload-' + Date.now() + '.json'),
                JSON.stringify(data, null, 2));
  } catch (_) { /* diagnostics are best effort */ }
}

exports.capture = function capture(data) {
  try {
    if (!data || typeof data !== 'object') return;
    fs.mkdirSync(DIR, { recursive: true });

    const windows = {};
    for (const name of Object.keys(WINDOWS)) {
      windows[name] = readWindow(data, WINDOWS[name]);
    }
    const present = !!(windows.five_hour || windows.seven_day);

    dumpRawOnce(data);

    const now = Date.now();
    const out = {
      schema: SCHEMA,
      collector_version: VERSION,
      captured_at: new Date(now).toISOString(),
      captured_at_epoch_ms: now,
      source: 'statusline',
      windows: windows,
      context_window: data.context_window || null,
      model: (data.model && (data.model.display_name || data.model.id)) || null,
      session_id: data.session_id || data.sessionId || null,
      probe: {
        rate_limits_present: present,
        top_level_keys: Object.keys(data),
        fallback_hits: present ? [] : scanFallback(data).slice(0, 8),
      },
    };

    // Skip the write when nothing meaningful changed and the file is recent.
    // The status line is respawned on every render, so this cuts disk churn.
    try {
      const prev = JSON.parse(fs.readFileSync(STATE, 'utf8'));
      const same = JSON.stringify(prev.windows) === JSON.stringify(out.windows);
      if (same && (now - (prev.captured_at_epoch_ms || 0)) < 60000) return;
    } catch (_) { /* no previous state, or unreadable: just write */ }

    writeAtomic(STATE, JSON.stringify(out));
  } catch (_) {
    // Fail open. The status line must always render.
  }
};

// Also usable standalone:  <payload.json node clausage_collector.js
if (require.main === module) {
  const chunks = [];
  process.stdin.on('data', (d) => chunks.push(d));
  process.stdin.on('end', () => {
    try {
      exports.capture(JSON.parse(chunks.join('')));
      process.stdout.write('captured -> ' + STATE + '\n');
    } catch (err) {
      process.stdout.write('failed: ' + err.message + '\n');
    }
  });
}
