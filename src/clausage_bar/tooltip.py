"""Tooltip and details text.

A tray tooltip is plain text in a fixed 128-character field
(``NOTIFYICONDATA.szTip``) -- no colours, no fonts, no markup, and no more
than 127 usable characters. Layout is the only lever.

The tooltip used to spend about a third of that budget on meters built from
block characters, which made it read like debug output. Measured against the
cap, every alternative was affordable:

    text only, with tier and resets     118 chars
    with 10-cell meters, no resets      105 chars
    with meters and resets              120 chars

So the meters were not dropped to save room. They were dropped because a
number and a clock time are what the eye wants from something visible for two
seconds, and 24 block glyphs beside them are noise. The freed budget bought a
title, the plan tier, the reset times and a source line.

Meters still belong in ``details()``, which has no cap and is read rather than
glanced at, and in the panel, which can draw real ones.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import config, icon, theme
from .freshness import STALE, UNKNOWN, fmt_ago, fmt_until
from .model import Eff, Snapshot

BAR_CELLS = 8
BAR_FULL = "█"    # full block
BAR_EMPTY = "░"   # light shade


def bar(pct: float | None, cells: int = BAR_CELLS) -> str:
    """A block-character meter. Used by details(), which has no length cap."""
    if pct is None:
        return BAR_EMPTY * cells
    filled = max(0, min(cells, int(round(pct / (100.0 / cells)))))
    # Any non-zero usage should show at least one cell, or 3% looks like 0%.
    if filled == 0 and pct > 0:
        filled = 1
    return BAR_FULL * filled + BAR_EMPTY * (cells - filled)


def _clock(target: datetime | None, now: datetime) -> str:
    if target is None:
        return "-"
    local = target.astimezone()
    if local.date() == now.astimezone().date():
        return local.strftime("%H:%M")
    return local.strftime("%a %H:%M")


# ---------------------------------------------------------------- tooltip

TITLE = "Claude Usage"
DOT = " · "                 # a thin separator, not a pipe

# The one place the tooltip's column widths live. 8 for the label fits
# "Credits"; a 4-wide percentage column keeps 9%, 60% and 100% aligned on the
# same right edge, which is what lets three rows be read as a table.
_LABEL_W = 8
_PCT_W = 4
_GAP = "  "

# The row the tray badge is standing for. Hovering should explain the number
# in the taskbar, and without this the tooltip listed three percentages and
# left the user to work out which one the badge was.
BADGE_MARK = "› "        # a single angle quote; two columns
BADGE_BLANK = "  "


def _row(label: str, pct: float, detail: str, marked: bool = False) -> str:
    return "{0}{1:<{2}}{3:>{4}}{5}{6}".format(
        BADGE_MARK if marked else BADGE_BLANK,
        label, _LABEL_W, "{0:.0f}%".format(pct), _PCT_W, _GAP, detail).rstrip()


def _tier(snapshot: Snapshot | None) -> str:
    """The team name, falling back to the plan tier.

    Named _tier for history; it is really "who this quota belongs to". On a
    team seat that is the organisation, and 127 characters are too few to
    spend on both.
    """
    if snapshot is None:
        return ""
    org = snapshot.account.get("org_name")
    if org:
        return str(org)
    tier = snapshot.account.get("seat_tier")
    return str(tier).replace("_", " ").title() if tier else ""


def compose(snapshot: Snapshot | None, effs: dict[str, Eff], health: str,
            age_s: float, note: str | None = None,
            now: datetime | None = None) -> str:
    """Multi-line tooltip, trimmed to fit TOOLTIP_MAX."""
    now = now or datetime.now(timezone.utc)

    if snapshot is None or health == UNKNOWN or not effs:
        text = TITLE + "\nNo reading yet"
        if note:
            text += "\n" + note
        return text[:config.TOOLTIP_MAX]

    stale = health == STALE

    session, weekly = effs.get("five_hour"), effs.get("seven_day")
    shown = icon.badge_window(session.pct if session else None,
                              weekly.pct if weekly else None)

    def window_row(key: str, eff: Eff | None) -> str | None:
        if eff is None or eff.pct is None:
            return None
        detail = "window reset" if eff.inferred else _clock(eff.next_reset, now)
        return _row(theme.SHORT_LABELS[key], eff.pct,
                    "" if detail == "-" else detail, marked=key == shown)

    required = [r for r in (window_row("five_hour", session),
                            window_row("seven_day", weekly))
                if r]

    credits: list[str] = []
    spend = snapshot.spend
    if spend and spend.enabled:
        detail = ""
        if spend.used_amount is not None and spend.limit_amount is not None:
            detail = "{0} / {1}".format(
                theme.money(spend.used_amount, spend.currency, 0),
                theme.money(spend.limit_amount, spend.currency, 0))
        credits.append(_row(theme.SHORT_LABELS["credits"], spend.percent,
                            detail))       # never the badge; never marked

    tier = _tier(snapshot)
    ago = fmt_ago(age_s)
    if stale:
        # Wording is the only lever plain text has for "not current".
        long_footer = "Last seen {0} ago{1}stale".format(ago, DOT)
        short_footer = "Stale, {0} old".format(ago)
    else:
        source = snapshot.source or ""
        long_footer = ("Updated {0} ago{1}{2}".format(ago, DOT, source)
                       if source else "Updated {0} ago".format(ago))
        short_footer = "Updated {0} ago".format(ago)

    # What to give up, and in what order, when 127 characters are not enough.
    #
    # These are weights, not nested loops. Nesting encodes the priority in the
    # *shape* of the code, and it got it wrong: with the title as the innermost
    # loop, a tooltip one character over budget dropped the app's own name
    # while still spending fifteen characters on the plan tier.
    DROP_TIER, DROP_SOURCE, DROP_CREDITS, DROP_TITLE = 1, 2, 4, 8

    variants = []
    for keep_title in (True, False):
        for keep_tier in (True, False):
            for keep_source in (True, False):
                for keep_credits in (True, False):
                    if keep_tier and not tier:
                        continue                # nothing there to keep
                    cost = ((0 if keep_tier else DROP_TIER)
                            + (0 if keep_source else DROP_SOURCE)
                            + (0 if keep_credits else DROP_CREDITS)
                            + (0 if keep_title else DROP_TITLE))
                    variants.append((cost, keep_title, keep_tier,
                                     keep_source, keep_credits))
    variants.sort(key=lambda v: v[0])

    for _cost, keep_title, keep_tier, keep_source, keep_credits in variants:
        parts: list[str] = []
        if keep_title:
            parts.append(TITLE + DOT + tier if (keep_tier and tier) else TITLE)
        parts = parts + required
        if keep_credits:
            parts = parts + credits
        if note:
            parts = parts + [note]
        parts = parts + [long_footer if keep_source else short_footer]
        text = "\n".join(parts)
        if len(text) <= config.TOOLTIP_MAX:
            return text

    # Only the two windows survive; everything else has been given up already.
    text = "\n".join(required)
    if len(text) <= config.TOOLTIP_MAX:
        return text
    return text[:config.TOOLTIP_MAX - 1] + "…"


# ---------------------------------------------------------------- details


def details(snapshot: Snapshot | None, effs: dict[str, Eff], health: str,
            age_s: float, note: str | None = None,
            now: datetime | None = None) -> str:
    """Plain-text breakdown, for the clipboard and --diagnose. No length cap.

    Meters are welcome here: this is read, not glanced at, and it is the one
    surface whose job is to be pasted into a bug report.
    """
    now = now or datetime.now(timezone.utc)
    lines = ["Claude usage limits"]

    if snapshot is None:
        lines += ["", "No data yet."]
        if note:
            lines.append(note)
        return "\n".join(lines)

    tier = _tier(snapshot)
    if tier:
        lines[0] += "   ({0})".format(tier)
    lines.append("")

    for name in ("five_hour", "seven_day", "seven_day_opus",
                 "seven_day_sonnet"):
        eff = effs.get(name)
        if eff is None or eff.pct is None:
            continue
        lines.append("  {0:<20} {1}  {2:>5.1f}% used".format(
            theme.LONG_LABELS[name], bar(eff.pct, 20), eff.pct))
        detail = "    "
        if eff.next_reset is not None:
            remaining = fmt_until(eff.next_reset, now)
            detail += "resets {0}".format(_clock(eff.next_reset, now))
            if remaining:
                detail += " (in {0})".format(remaining)
        if eff.inferred:
            detail += "   [{0}]".format(eff.note)
        if detail.strip():
            lines.append(detail)
        lines.append("")

    spend = snapshot.spend
    if spend:
        lines.append("  {0:<20} {1}  {2:>5.1f}% used".format(
            theme.LONG_LABELS["credits"], bar(spend.percent, 20),
            spend.percent))
        if spend.used_amount is not None and spend.limit_amount is not None:
            lines.append("    {0} of {1}{2}".format(
                theme.money(spend.used_amount, spend.currency),
                theme.money(spend.limit_amount, spend.currency),
                "" if spend.enabled else "   (disabled)"))
        if spend.limit_reached:
            lines.append("    spend limit reached")
        lines.append("")

    lines.append("  source {0}   updated {1} ago   {2}".format(
        snapshot.source, fmt_ago(age_s), health))
    if note:
        lines.append("  note: {0}".format(note))
    return "\n".join(lines)
