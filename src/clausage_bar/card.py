"""The hover card: a real usage card, drawn with Pillow.

Windows gives a tray icon one tooltip, ``NOTIFYICONDATA.szTip``: 127
characters of plain text, no colour, no fonts, no markup. However carefully
that field is laid out it reads as console output, because it *is* console
output. There is no version of it that looks like a product.

So this module rasterises a card instead -- dark surface, rounded corners,
real type, real progress bars, the same palette and the same ring colours as
the panel -- and ``hover.py`` puts it on screen in a layered window when the
cursor rests on the tray icon.

Everything here is pure Pillow and returns an image. Nothing in this module
touches Windows, which is what makes the card renderable straight to a PNG in
a test.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

from . import freshness, theme
from .freshness import STALE, UNKNOWN, fmt_ago, fmt_until
from .model import Eff, Snapshot

# Geometry. Chosen so the card is a little narrower than the panel: it is a
# summary, and matching the panel's width would invite comparison as an equal
# rather than as a preview of it.
WIDTH = 296
PAD = 15
ROW_GAP = 15
BAR_H = 6
BAR_RADIUS = 3
RADIUS = theme.CARD_RADIUS
SHADOW = 6                  # margin the shadow needs outside the card

TITLE_PT = 13
LABEL_PT = 11
PCT_PT = 11
SUB_PT = 9
FOOT_PT = 9

# Supersample the whole card. The rounded corners and the bar caps are curves,
# and a curve drawn at final size is a staircase. Text is drawn at the final
# size instead -- see _text().
SS = 3


@lru_cache(maxsize=32)
def _font(weight: str, size: int):
    from PIL import ImageFont
    for path in theme.FONT_FILES.get(weight, ()):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _round_rect(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline,
                           width=width)


def _text(draw, xy, text, weight, size, colour, anchor="la"):
    draw.text(xy, text, font=_font(weight, size), fill=colour, anchor=anchor)


def _mark(draw, cx, cy, radius, colour, width=2):
    """Claude's asterisk, drawn rather than typed.

    Set as text it came out as a tofu box: Segoe UI has no U+2733, and a
    missing glyph in the header is the first thing anyone would notice. Eight
    spokes is the shape, and geometry always renders.
    """
    import math
    for index in range(8):
        angle = math.radians(index * 45.0)
        draw.line((cx + math.cos(angle) * radius * 0.30,
                   cy + math.sin(angle) * radius * 0.30,
                   cx + math.cos(angle) * radius,
                   cy + math.sin(angle) * radius),
                  fill=colour, width=width)


def _bar(draw, x0, y, x1, pct, colour, track):
    """A rounded track with a rounded fill."""
    _round_rect(draw, (x0, y, x1, y + BAR_H), BAR_RADIUS, track)
    if pct is None or pct <= 0:
        return
    span = (x1 - x0) * min(float(pct), 100.0) / 100.0
    # A rounded fill narrower than its own diameter degenerates into a lens,
    # so anything visible gets at least one cap width.
    span = max(BAR_H, span)
    _round_rect(draw, (x0, y, x0 + span, y + BAR_H), BAR_RADIUS, colour)


class Row:
    """One line of the card: a label, a bar, a percentage and a sub-line."""

    __slots__ = ("label", "pct", "sub", "state")

    def __init__(self, label: str, pct: float | None, sub: str, state: str):
        self.label = label
        self.pct = pct
        self.sub = sub
        self.state = state


def rows_for(snapshot: Snapshot | None, effs: dict[str, Eff],
             now: datetime) -> list[Row]:
    """The card's three rows, in the panel's order and wording."""
    out: list[Row] = []
    for name in ("five_hour", "seven_day"):
        eff = effs.get(name)
        if eff is None or eff.pct is None:
            continue
        if eff.inferred:
            sub = "Window reset"
        elif eff.next_reset is None:
            sub = "Reset time unknown"
        else:
            remaining = fmt_until(eff.next_reset, now)
            local = eff.next_reset.astimezone()
            when = local.strftime("%I:%M %p").lstrip("0")
            if local.date() != now.astimezone().date():
                when = local.strftime("%a ") + when
            sub = ("Resets in {0}".format(_humanize(remaining))
                   if remaining else "Resets {0}".format(when))
        out.append(Row(theme.LONG_LABELS[name], eff.pct, sub,
                       theme.state_for(eff.pct)))

    spend = snapshot.spend if snapshot else None
    if spend:
        sub = "Covers you past your plan limits"
        if spend.used_amount is not None and spend.limit_amount is not None:
            sub = "{0} of {1}".format(
                theme.money(spend.used_amount, spend.currency),
                theme.money(spend.limit_amount, spend.currency))
        if not spend.enabled:
            sub += "   ·   disabled"
        out.append(Row(theme.LONG_LABELS["credits"], spend.percent, sub,
                       theme.state_for(spend.percent)))
    return out


def _humanize(short: str) -> str:
    """'2h33m' -> '2h 33m';  '5d' -> '5 days'. Compact, for a summary."""
    if short.endswith("d"):
        days = short[:-1]
        return "{0} day{1}".format(days, "" if days == "1" else "s")
    if short.endswith("s") and "h" not in short and "m" not in short:
        return short[:-1] + " sec"
    return short.replace("h", "h ").strip()


# One row's vertical budget, spelled out so the height calculation and the
# drawing code cannot disagree -- they did, and the sub-line ended up sitting
# on the next row's label.
LABEL_H = 16
LABEL_TO_BAR = 7
BAR_TO_SUB = 7
SUB_H = 13
ROW_H = LABEL_H + LABEL_TO_BAR + BAR_H + BAR_TO_SUB + SUB_H
HEAD_H = 20 + 16
FOOT_H = 14 + 14


def card_height(row_count: int) -> int:
    body = row_count * ROW_H + max(0, row_count - 1) * ROW_GAP
    return PAD + HEAD_H + body + FOOT_H + PAD


def render(snapshot: Snapshot | None, effs: dict[str, Eff], health: str,
           age_s: float, note: str | None = None,
           now: datetime | None = None) -> Image.Image:
    """The whole card, as an RGBA image with a soft shadow around it."""
    now = now or datetime.now(timezone.utc)
    stale = health == STALE
    rows = [] if health == UNKNOWN else rows_for(snapshot, effs, now)

    inner_h = card_height(len(rows)) if rows else (PAD * 2 + 52)
    total_w = WIDTH + 2 * SHADOW
    total_h = inner_h + 2 * SHADOW

    big = Image.new("RGBA", (total_w * SS, total_h * SS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)

    # A soft shadow, done as three concentric rounded rectangles rather than a
    # blur: at this radius a Gaussian is indistinguishable and costs a filter
    # pass on every hover.
    for step in range(SHADOW, 0, -2):
        alpha = int(theme.CARD_SHADOW_ALPHA * (1 - step / (SHADOW + 2.0)))
        _round_rect(draw,
                    ((SHADOW - step) * SS, (SHADOW - step) * SS,
                     (total_w - SHADOW + step) * SS - 1,
                     (total_h - SHADOW + step) * SS - 1),
                    (RADIUS + step) * SS, (0, 0, 0, max(0, alpha)))

    box = (SHADOW * SS, SHADOW * SS,
           (SHADOW + WIDTH) * SS - 1, (SHADOW + inner_h) * SS - 1)
    _round_rect(draw, box, RADIUS * SS, theme.CARD_BG + (255,),
                outline=theme.CARD_BORDER + (255,), width=max(1, SS))

    # Text and bars are drawn after the downsample, at final size: Pillow
    # already antialiases glyphs, and putting them through the resize as well
    # only softens edges that were exact.
    flat = big.resize((total_w, total_h), Image.LANCZOS)
    pen = ImageDraw.Draw(flat)

    x0 = SHADOW + PAD
    x1 = SHADOW + WIDTH - PAD
    y = SHADOW + PAD

    # ---- header -------------------------------------------------------
    mark = theme.CLAUDE_CORAL_RGB if not stale else theme.accent(
        theme.UNKNOWN, True)
    _mark(pen, x0 + 6, y + 8, 6.5, mark + (255,))
    _text(pen, (x0 + 19, y), "Claude Usage", "semibold", TITLE_PT,
          theme.TEXT_RGB + (255,))
    # The team rather than the plan tier, matching the panel's header.
    label = ""
    if snapshot is not None:
        label = snapshot.account.get("org_name") or ""
        if not label:
            raw = snapshot.account.get("seat_tier")
            label = str(raw).replace("_", " ").title() if raw else ""
    if label:
        _text(pen, (x1, y + 3), label, "regular", SUB_PT,
              theme.TEXT_FAINT_RGB + (255,), anchor="ra")
    y += HEAD_H

    # ---- rows ---------------------------------------------------------
    if not rows:
        _text(pen, (x0, y), "No reading yet", "semibold", LABEL_PT,
              theme.TEXT_MUTED_RGB + (255,))
        y += 18
        _text(pen, (x0, y), note or "Open Claude Code once to refresh.",
              "regular", SUB_PT, theme.TEXT_FAINT_RGB + (255,))
        return flat

    for index, row in enumerate(rows):
        colour = theme.accent(row.state, stale)
        _text(pen, (x0, y), row.label, "semibold", LABEL_PT,
              (theme.TEXT_MUTED_RGB if stale else theme.TEXT_RGB) + (255,))
        _text(pen, (x1, y), "{0:.0f}%".format(row.pct), "semibold", PCT_PT,
              colour + (255,), anchor="ra")
        y += LABEL_H + LABEL_TO_BAR
        _bar(pen, x0, y, x1, row.pct, colour + (255,),
             theme.CARD_TRACK + (255,))
        y += BAR_H + BAR_TO_SUB
        _text(pen, (x0, y), row.sub, "regular", SUB_PT,
              theme.TEXT_FAINT_RGB + (255,))
        y += SUB_H + (ROW_GAP if index < len(rows) - 1 else 0)

    # ---- footer -------------------------------------------------------
    y += 14
    dot = theme.accent(theme.UNKNOWN, True) if stale else theme.accent(
        theme.CALM)
    pen.ellipse((x0, y + 4, x0 + 6, y + 10), fill=dot + (255,))
    status = ("Last read {0} ago  ·  not current".format(fmt_ago(age_s))
              if stale else
              "Updated {0} ago  ·  via {1}".format(
                  fmt_ago(age_s), snapshot.source if snapshot else "?"))
    _text(pen, (x0 + 12, y), status, "regular", FOOT_PT,
          (theme.TEXT_MUTED_RGB if stale else theme.TEXT_FAINT_RGB) + (255,))
    if note:
        y += 13
        _text(pen, (x0, y), note, "regular", FOOT_PT,
              theme.accent(theme.WARNING) + (255,))

    return flat


def render_state(snapshot: Snapshot | None,
                 now: datetime | None = None) -> Image.Image:
    """Convenience: evaluate freshness and render, for previews and tests."""
    now = now or datetime.now(timezone.utc)
    effs, health, age = freshness.evaluate(snapshot, now)
    return render(snapshot, effs, health, age, now=now)
