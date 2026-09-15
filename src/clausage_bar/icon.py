"""Tray badge rendering.

The badge is a **progress ring**: a muted track with an arc swept clockwise
from twelve o'clock, coloured by state. At 16-24px a shape carries further
than a numeral does, and this one says "meter" before it says anything else.

Why not the digits it replaced (still available as CLAUSAGE_ICON_STYLE=digits):
two glyphs in a 16px square have to be so tightly packed that "11" and "II"
and a pause bar become the same picture, which is what they looked like in
practice. And why no numeral inside the ring, which was the obvious hybrid:
white ink inside a ring has no background of its own, so on a *light* taskbar
it simply disappears. Rendered candidates at true size are in the project
notes; the ring was the only one that survived both taskbars.

The ring is drawn at 8x and downsampled with Lanczos, with circles at both
ends of the arc to fake the round caps Pillow's arc() will not give us. Below
about 0.18 of the icon width the stroke stops reading as a ring and becomes a
grey circle, so RING_STROKE does not go thinner than that.

Everything below the ring section is the older digit badge, kept because it is
the fallback when a ring cannot be drawn and because its measurements were
expensive to learn:

Four things learned the hard way, all of which the code still depends on:

*A tile can never match the taskbar.* Whatever colour we fill the background
with will differ slightly from the taskbar's own, and a 16x16 square of
almost-right colour reads as a visible box stuck to the bar. Only alpha 0
disappears. Hence "plain" is the default; "dark" and "bands" remain for anyone
who wants the colour signal in the icon itself.

*The slot is fixed, but not at 16px -- and asking the wrong way is worse than
not asking.* Windows gives a tray icon exactly SM_CXSMICON pixels: 16 at 100%
display scaling, 24 at 150%. A process that has not declared DPI awareness is
told 16 whatever the truth is, and Windows then stretches its 16px bitmap to
the 24px the tray really uses -- a 1.5x upscale of hand-plotted pixels, which
is precisely the mush it sounds like. So the entry point declares per-monitor
awareness before any window exists (see declare_dpi_aware in __main__) and this
module renders at whatever size it is actually handed. For a long time it did
not, and every careful measurement here was made against a lie.

*Downscaling destroys small text.* A TrueType face rendered large and shrunk to
16px turns to mush, so the pixel faces are plotted dot by dot at the real size
and TrueType is thresholded rather than antialiased. In the pixel faces "1"
carries a flag and a foot serif; without them it reads as a lowercase L and
"100" looks like "IOO".

*Staleness must survive every style.* "plain" and "dark" have no band fill to
dim, so the ink greys out instead. Without that a stale reading was once
pixel-identical to a fresh one.
"""

from __future__ import annotations

import ctypes
import math
import os
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

from . import config, theme

# Primary face: 5 wide, 7 tall, heavy stems so it stays solid a few pixels
# high. "1" is 4 wide -- narrower, which is what lets "100" fit in 16px.
FONT_LARGE = {
    "0": ("11111", "11011", "11011", "11011", "11011", "11011", "11111"),
    "1": ("0110", "1110", "0110", "0110", "0110", "0110", "1111"),
    "2": ("11111", "00011", "00011", "11111", "11000", "11000", "11111"),
    "3": ("11111", "00011", "00011", "11111", "00011", "00011", "11111"),
    "4": ("11011", "11011", "11011", "11111", "00011", "00011", "00011"),
    "5": ("11111", "11000", "11000", "11111", "00011", "00011", "11111"),
    "6": ("11111", "11000", "11000", "11111", "11011", "11011", "11111"),
    "7": ("11111", "00011", "00011", "00011", "00011", "00011", "00011"),
    "8": ("11111", "11011", "11011", "11111", "11011", "11011", "11111"),
    "9": ("11111", "11011", "11011", "11111", "00011", "00011", "11111"),
    "?": ("11111", "00011", "00011", "01111", "01100", "00000", "01100"),
}

# For 24px+ icons (125%/150% display scaling). Measured against the Windows
# IME "ENG / IN" indicator, whose glyphs are ~8.7px tall in a 24px-tall button:
# at 10px tall this face matches and exceeds it.
FONT_XL = {
    "0": ("1111111", "1111111", "1100011", "1100011", "1100011", "1100011",
          "1100011", "1100011", "1111111", "1111111"),
    "1": ("00110", "01110", "11110", "00110", "00110", "00110", "00110",
          "00110", "11111", "11111"),
    "2": ("1111111", "1111111", "0000011", "0000011", "1111111", "1111111",
          "1100000", "1100000", "1111111", "1111111"),
    "3": ("1111111", "1111111", "0000011", "0000011", "1111111", "1111111",
          "0000011", "0000011", "1111111", "1111111"),
    "4": ("1100011", "1100011", "1100011", "1100011", "1111111", "1111111",
          "0000011", "0000011", "0000011", "0000011"),
    "5": ("1111111", "1111111", "1100000", "1100000", "1111111", "1111111",
          "0000011", "0000011", "1111111", "1111111"),
    "6": ("1111111", "1111111", "1100000", "1100000", "1111111", "1111111",
          "1100011", "1100011", "1111111", "1111111"),
    "7": ("1111111", "1111111", "0000011", "0000011", "0000011", "0000011",
          "0000011", "0000011", "0000011", "0000011"),
    "8": ("1111111", "1111111", "1100011", "1100011", "1111111", "1111111",
          "1100011", "1100011", "1111111", "1111111"),
    "9": ("1111111", "1111111", "1100011", "1100011", "1111111", "1111111",
          "0000011", "0000011", "1111111", "1111111"),
    "?": ("1111111", "1111111", "0000011", "0000011", "0111111", "0111110",
          "0110000", "0000000", "0110000", "0110000"),
}

# Fallback for anything the large face cannot fit (very small icons).
FONT_SMALL = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("110", "010", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "001", "001", "001"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    "?": ("111", "001", "011", "000", "010"),
}

TRACKING = 1
DEFAULT_SIZE = 16
WHITE = (255, 255, 255)

# Breathing room inside the tile, as a fraction of its size. The tile itself is
# full-bleed -- it needs every pixel of the slot to read as a deliberate chip
# rather than a stray square -- so all the air has to come from insetting the
# digits instead. At 16px this is 3px above and below, 2px at the sides.
#
# The two axes are deliberately different. Vertical padding is free and is what
# actually reads as "spread out"; horizontal padding is expensive, because two
# digits at a legible stroke weight already need ~13 of the 16 columns. An
# earlier version used a symmetric 1px inset, which left the numerals touching
# the tile edge and looked congested. 2px of side padding costs one column of
# glyph width, paid for by dropping the 6-wide face to the 5-wide one -- the
# stroke stays 2px, so nothing is lost but a column of counter.
PAD_Y_RATIO = 0.16
PAD_X_RATIO = 0.12

# Letter-spacing between digits, tried before falling back to 1px.
SEG_TRACKING = 2

# "light" = dark ink on a pale, level-tinted tile. The clearest option by a
#           wide margin: a bright tile stands out against a dark taskbar and
#           dark-on-pale is the highest-contrast pairing available (~14:1).
# "plain" = no tile; white digits straight onto the taskbar, the way the IME
#           "ENG / IN" indicator is drawn. Nothing to mismatch, but the ink is
#           all the contrast there is.
# "dark"  = white ink on one dark tile.
# "bands" = white ink on level-colored bands.
# "number" = a tinted plate with the percentage, as large as the slot allows.
#            The default: at 24px the numeral is what carries the reading.
# "ring"   = the progress ring, with a smaller number inside it.
# "digits"  (aliases: light/dark/bands/plain) = the original numeric badge.
STYLE = os.environ.get("CLAUSAGE_ICON_STYLE", "number").strip().lower()

# The numeric badge's own sub-styles, kept working under CLAUSAGE_ICON_STYLE.
_DIGIT_STYLES = ("digits", "light", "dark", "bands", "plain")

# "pixel" = the hand-plotted faces above.
# "serif" = real Times New Roman, "sans" = Segoe UI. Both are TrueType, so
# they are rendered through a threshold rather than antialiased: grey edge
# pixels on a 7px-tall glyph read as blur, not as smoothing.
FONT_FAMILY = os.environ.get("CLAUSAGE_ICON_FONT", "serif").strip().lower()

# Nudge the TrueType size up or down; the fitter already maximises it.
FONT_BOOST = 0
try:
    FONT_BOOST = int(os.environ.get("CLAUSAGE_ICON_FONT_BOOST", "0"))
except ValueError:
    pass

_TTF_PATHS = {
    "serif": ("C:/Windows/Fonts/timesbd.ttf", "C:/Windows/Fonts/times.ttf"),
    "sans": ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf"),
}
_TTF_THRESHOLD = 110      # below this the glyph edge is dropped, keeping it crisp

# "white" matches the IME indicator; "level" tints the digits green/amber/red
# for styles that have no band to carry the colour.
INK_MODE = os.environ.get("CLAUSAGE_ICON_INK", "white").strip().lower()

# Rows in the badge. At 16px two stacked numbers cannot be legible: each row
# gets 7px against ENG/IN's ~8.7px, with no room for the leading that makes
# ENG/IN readable in the first place. One number gets the whole slot and
# roughly doubles in height; the other window is read from the tooltip, panel
# or widget.
#
# At 24px (150% scaling) two rows genuinely work -- 9px glyphs with a 2px
# stroke, against ENG/IN's 8.7px -- so CLAUSAGE_ICON_ROWS=2 is a real option
# there rather than a compromise.
ICON_ROWS = 1
try:
    ICON_ROWS = 1 if int(os.environ.get("CLAUSAGE_ICON_ROWS", "1")) <= 1 else 2
except ValueError:
    pass


def tray_icon_size() -> int:
    """The pixel size Windows actually wants, DPI scaling included."""
    try:
        size = int(ctypes.windll.user32.GetSystemMetrics(49))  # SM_CXSMICON
        if 8 <= size <= 256:
            return size
    except (AttributeError, OSError, ValueError):
        pass
    return DEFAULT_SIZE


# Which window the badge stands for. Defined once here and read by the tooltip
# and the panel, so all three surfaces agree on what the number in the tray
# means. Duplicating this rule was how the badge and the tooltip could
# disagree about the same reading.
BADGE_WINDOWS = ("five_hour", "seven_day")


def badge_window(session_pct: float | None,
                 weekly_pct: float | None) -> str | None:
    """The window the badge stands for: the current session.

    It used to be whichever window was higher, on the reasoning that a calm
    badge beside a nearly-spent weekly window would be a lie. But that made
    the badge ambiguous in the other direction -- the number silently switched
    meaning between the two windows, so "33" could be either, and there was no
    way to tell from the taskbar which one you were looking at.

    A badge that always means the same thing is worth more than one that
    always shows the larger number. The weekly window is one hover away, and
    it still gets its own alerts at 50/75/100.

    Falls back to the weekly window only when there is no session reading at
    all, since a blank badge is worse than the wrong window.
    """
    if session_pct is not None:
        return "five_hour"
    if weekly_pct is not None:
        return "seven_day"
    return None


def level_for(pct: float | None) -> str:
    if pct is None:
        return "unknown"
    if pct >= 75:
        return "hot"
    if pct >= 50:
        return "warn"
    return "ok"


def badge_text(pct: float | None) -> str:
    if pct is None:
        return "?"
    return str(max(0, min(999, int(round(pct)))))


def _glyph(font: dict, char: str) -> tuple[str, ...]:
    return font.get(char, font["?"])


def _font_height(font: dict) -> int:
    return len(font["0"])


def _text_width(font: dict, text: str, scale: int = 1,
                tracking: int = TRACKING) -> int:
    if not text:
        return 0
    glyphs = sum(len(_glyph(font, c)[0]) for c in text)
    return (glyphs + tracking * (len(text) - 1)) * scale


def _fit(text: str, width: int, height: int) -> tuple[dict, int]:
    """Largest (face, scale) whose rendering fits the given box.

    Ordered biggest-first so a 24px icon at 150% scaling actually uses the
    extra pixels instead of centring 16px-sized digits in it.
    """
    for font in (FONT_XL, FONT_LARGE, FONT_SMALL):
        for scale in range(4, 0, -1):
            if (_text_width(font, text, scale) <= width
                    and _font_height(font) * scale <= height):
                return font, scale
    return FONT_SMALL, 1


# ---------------------------------------------------------------- segment face
#
# Digits composed from seven segments rather than hand-typed bitmaps or a
# TrueType face. Two reasons:
#
#   * Width is the binding constraint. Every TrueType face measured needs
#     20-30px to set two digits at a 16px inked height, because each glyph
#     carries side bearings. Plotting them means zero side bearings, so two
#     digits fit in 15px and can be the full height of the icon.
#   * The stroke stays thick at any size, which is what actually makes a small
#     numeral readable. Weight beats size below about 10px.
#
# Digits are the same width by construction, so the badge never jitters as the
# value changes.

_SEGMENTS = {
    "0": "T UL UR LL LR B",
    "1": "UR LR",
    "2": "T UR M LL B",
    "3": "T UR M LR B",
    "4": "UL UR M LR",
    "5": "T UL M LR B",
    "6": "T UL M LL LR B",
    "7": "T UR LR",
    "8": "T UL UR M LL LR B",
    "9": "T UL UR M LR B",
    # Seven segments cannot make a legible question mark at this size, and the
    # attempt read as a broken bracket. A centred bar says "no reading" plainly.
    "?": "M",
}

# The faces to try, widest first, as (glyph width, width of "1", stroke).
#
# Derived from the glyph height rather than hard-coded, because the badge is
# not always 16px: at 150% display scaling the tray slot is 24px, and a ladder
# tuned for a 10px glyph left 16px-tall digits with spindly 2px strokes.
#
# The counter -- the hole in 0 and 8 -- is what is left over after the two
# stems, so a stroke is only allowed if at least one column survives. That
# clamp is what keeps a narrow face from going solid.
# Width as a fraction of the glyph height. The widest rung is ~0.58 because a
# digit much wider than that stops looking like a digit: at 0.70 the strokes
# cover so much of the cell that dark-on-pale reads as a solid blob with a few
# pale nicks in it, which is what the two-row badge looked like at first.
# The last, narrowest rung exists only for three digits ("100") in a slot
# sized for two. Dropping it once made 100% unrenderable.
_SEG_WIDTH_RATIOS = (0.58, 0.50, 0.44, 0.36, 0.30)


@lru_cache(maxsize=32)
def _seg_ladder(height: int) -> tuple[tuple[int, int, int], ...]:
    stroke = max(1, round(height / 5.0))
    ladder = []
    for ratio in _SEG_WIDTH_RATIOS:
        width = max(3, round(height * ratio))
        this_stroke = max(1, min(stroke, (width - 1) // 2))   # keep it open
        one_width = max(this_stroke + 1, round(width * 0.55))
        ladder.append((width, one_width, this_stroke))
    return tuple(ladder)


def _segment_glyph(width: int, height: int, stroke: int, segs: str) -> tuple:
    grid = [[0] * width for _ in range(height)]
    mid = (height - stroke) // 2

    def hbar(y0):
        for y in range(y0, min(height, y0 + stroke)):
            for x in range(width):
                grid[y][x] = 1

    def vbar(x0, y0, y1):
        for y in range(max(0, y0), min(height, y1)):
            for x in range(x0, min(width, x0 + stroke)):
                grid[y][x] = 1

    parts = set(segs.split())
    if "T" in parts:
        hbar(0)
    if "M" in parts:
        hbar(mid)
    if "B" in parts:
        hbar(height - stroke)
    if "UL" in parts:
        vbar(0, 0, mid + stroke)
    if "UR" in parts:
        vbar(width - stroke, 0, mid + stroke)
    if "LL" in parts:
        vbar(0, mid, height)
    if "LR" in parts:
        vbar(width - stroke, mid, height)
    return tuple("".join(str(v) for v in row) for row in grid)


def _one_glyph(width: int, height: int, stroke: int) -> tuple:
    """A "1" with a flag and a foot, which pure segments cannot express.

    Seven segments render 1 as a bare vertical bar, so "100" comes out as
    "IOO" -- the same trap the hand-plotted pixel font fell into and was fixed
    for. The flag (a step up-left from the stem) and the foot (a full-width
    bar) are what make the stroke read as a numeral instead of a rule.
    """
    grid = [[0] * width for _ in range(height)]
    stem_x = max(0, width - stroke)

    for y in range(height):
        for x in range(stem_x, width):
            grid[y][x] = 1

    flag_x = max(0, stem_x - stroke)
    for y in range(min(stroke, height)):
        for x in range(flag_x, stem_x):
            grid[y][x] = 1

    for y in range(max(0, height - stroke), height):
        for x in range(width):
            grid[y][x] = 1

    return tuple("".join(str(v) for v in row) for row in grid)


@lru_cache(maxsize=64)
def _segment_face(width: int, height: int, stroke: int, one_width: int) -> dict:
    face = {ch: _segment_glyph(width, height, stroke, segs)
            for ch, segs in _SEGMENTS.items()}
    # "1" is the one glyph that is not segment-shaped, and it needs at least
    # two columns for a flag and a foot to fit beside the stem.
    face["1"] = _one_glyph(max(one_width, stroke + 1), height, stroke)
    return face


@lru_cache(maxsize=128)
def _fit_segment(text: str, width: int, height: int,
                 tracking: int = TRACKING):
    """Widest segment face whose setting of `text` fits the box."""
    if height < 5:
        return None
    for glyph_w, one_w, stroke in _seg_ladder(height):
        face = _segment_face(glyph_w, height, stroke, one_w)
        if _text_width(face, text, 1, tracking) <= width:
            return face
    return None


def _fit_segment_spaced(text: str, width: int, height: int):
    """(face, tracking) preferring the loosest letter-spacing that still fits.

    Digits set shoulder to shoulder read as one blob at this size; a second
    pixel between them is the difference between "34" and a smear.
    """
    for tracking in (SEG_TRACKING, 1):
        face = _fit_segment(text, width, height, tracking)
        if face is not None:
            return face, tracking
    return None, 1


def _blit(draw: ImageDraw.ImageDraw, font: dict, text: str, x: int, y: int,
          color: tuple[int, int, int], scale: int,
          tracking: int = TRACKING) -> None:
    cursor = x
    for char in text:
        rows = _glyph(font, char)
        for row_i, row in enumerate(rows):
            for col_i, bit in enumerate(row):
                if bit == "1":
                    x0 = cursor + col_i * scale
                    y0 = y + row_i * scale
                    draw.rectangle((x0, y0, x0 + scale - 1, y0 + scale - 1),
                                   fill=color)
        cursor += (len(rows[0]) + tracking) * scale


@lru_cache(maxsize=64)
def _face(weight: str, size: int):
    """Load a real UI face at an exact pixel size, best available first."""
    from PIL import ImageFont
    for path in theme.FONT_FILES.get(weight, ()):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return None


@lru_cache(maxsize=128)
def _fit_face(weight: str, text: str, width: int, height: int):
    """Largest point size whose *inked* box fits, plus that box.

    TrueType metrics carry ascender and descender leading that digits never
    use, so the fit has to be measured on the ink rather than on the line
    height, or the numeral comes out a third smaller than the space allows.
    """
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    for size in range(int(height * 2.4) + 4, 4, -1):
        font = _face(weight, size)
        if font is None:
            return None
        box = probe.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= width and box[3] - box[1] <= height:
            return size, box
    return None


@lru_cache(maxsize=64)
def _ttf(family: str, size: int):
    """Load Times New Roman / Segoe UI at an exact pixel size."""
    from PIL import ImageFont
    for path in _TTF_PATHS.get(family, ()):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return None


@lru_cache(maxsize=256)
def _ttf_fit(family: str, text: str, width: int, height: int):
    """Largest point size whose inked bbox fits the box, plus that bbox.

    TrueType metrics include ascender/descender leading that digits never
    use, so the fit is measured on the actual inked bounding box.
    """
    from PIL import ImageDraw
    probe = Image.new("L", (max(width, 8) * 4, max(height, 8) * 6))
    draw = ImageDraw.Draw(probe)
    best = None
    for size in range(height * 3 + 6, 3, -1):
        font = _ttf(family, size)
        if font is None:
            return None
        box = draw.textbbox((0, 0), text, font=font)
        w, h = box[2] - box[0], box[3] - box[1]
        if w <= width and h <= height:
            best = (size, box)
            break
    return best


def _blit_ttf(image: Image.Image, family: str, text: str, box: tuple[int, int],
              size_box: tuple[int, int], color: tuple[int, int, int]) -> bool:
    """Draw thresholded TrueType text centred in the given band."""
    from PIL import ImageDraw
    width, band_h = size_box
    fitted = _ttf_fit(family, text, width, band_h)
    if fitted is None:
        return False
    size, bbox = fitted
    size = max(4, size + FONT_BOOST)
    font = _ttf(family, size)
    if font is None:
        return False

    mask = Image.new("L", (width, band_h), 0)
    md = ImageDraw.Draw(mask)
    bbox = md.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    md.text(((width - tw) / 2 - bbox[0], (band_h - th) / 2 - bbox[1]),
            text, font=font, fill=255)
    # Threshold: a half-lit pixel on a 7px glyph reads as blur, not smoothing.
    mask = mask.point(lambda v: 255 if v >= _TTF_THRESHOLD else 0)
    image.paste(color + (255,), (0, box[0]), mask)
    return True


def _dim(color: tuple[int, int, int]) -> tuple[int, int, int]:
    """Blend toward grey, so a stale reading is visibly not current."""
    r, g, b = config.STALE_BLEND
    return (int(color[0] * 0.5 + r * 0.5),
            int(color[1] * 0.5 + g * 0.5),
            int(color[2] * 0.5 + b * 0.5))


@lru_cache(maxsize=512)
def render(session_text: str, session_level: str,
           weekly_text: str, weekly_level: str,
           dimmed: bool, size: int, style: str = STYLE,
           family: str = FONT_FAMILY, rows: int = ICON_ROWS) -> Image.Image:
    """Two-row badge. Cached: the input space is small and it redraws often."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    top_color = config.COLORS.get(session_level, config.COLORS["unknown"])
    bottom_color = config.COLORS.get(weekly_level, config.COLORS["unknown"])
    if dimmed:
        top_color, bottom_color = _dim(top_color), _dim(bottom_color)

    if rows == 1:
        text = session_text if session_level != "unknown" else weekly_text
        level = session_level if session_level != "unknown" else weekly_level
        fill = config.COLORS.get(level, config.COLORS["unknown"])
        radius = max(2, size // 5)

        if style == "light":
            tint = config.PALE_COLORS.get(level, config.PALE_COLORS["unknown"])
            if dimmed:
                tint = config.PALE_COLORS["unknown"]
            draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius,
                                   fill=tint + (255,))
            ink = config.INK_STALE if dimmed else config.INK_DARK
        elif style == "bands":
            draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius,
                                   fill=fill + (255,))
            ink = config.STALE_BLEND if dimmed else WHITE
        elif style == "dark":
            draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius,
                                   fill=config.TILE_COLOR + (255,))
            ink = config.STALE_BLEND if dimmed else WHITE
        else:
            ink = (config.STALE_BLEND if dimmed
                   else (fill if INK_MODE == "level" else WHITE))

        pad_y = max(2, round(size * PAD_Y_RATIO))
        pad_x = max(1, round(size * PAD_X_RATIO))
        inner_w = size - 2 * pad_x
        inner_h = size - 2 * pad_y
        face, tracking = _fit_segment_spaced(text, inner_w, inner_h)
        if face is not None:
            width = _text_width(face, text, 1, tracking)
            x = pad_x + (inner_w - width) // 2
            _blit(draw, face, text, x, pad_y, ink + (255,), 1, tracking)
            return image

        box = (0, size - 1)
        if not (family in _TTF_PATHS
                and _blit_ttf(image, family, text, box, (size, size), ink)):
            font, scale = _fit(text, size, size)
            x = (size - _text_width(font, text, scale)) // 2
            y = max(0, (size - _font_height(font) * scale) // 2)
            _blit(draw, font, text, x, y, ink + (255,), scale)
        return image

    # Gutter between the rows. Two pixels at 16px reads as intentional
    # separation; a single-pixel hairline reads as a rendering artefact.
    # Splitting 16 as 7 / 2 / 7 also leaves no wasted outer padding.
    gutter = max(2, round(size * 0.125)) if style != "light" else         max(2, round(size * 0.10))
    top_h = (size - gutter) // 2
    top_box = (0, top_h - 1)
    bottom_box = (top_h + gutter, size - 1)
    seam_y, seam = top_h, gutter

    # On the banded style the dimming lands on the fills, but "dark" and
    # "plain" have no fill to dim -- so the ink has to carry it, or a stale
    # reading would be pixel-identical to a fresh one and the whole staleness
    # signal would be silently lost.
    ink = config.STALE_BLEND if dimmed else WHITE

    if style == "light":
        # The same pairing the one-row badge uses, and for the same reason:
        # dark ink on a pale tint measures ~15:1, white on a mid-green band
        # only 3.0:1 -- below the 4.5:1 that AA asks for small text.
        radius = max(2, size // 5)
        draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius,
                               fill=config.PALE_COLORS["unknown"] + (255,))
        for level, box in ((session_level, top_box), (weekly_level, bottom_box)):
            tint = config.PALE_COLORS.get(level, config.PALE_COLORS["unknown"])
            if dimmed:
                tint = config.PALE_COLORS["unknown"]
            draw.rounded_rectangle((0, box[0], size - 1, box[1]),
                                   radius=max(1, radius - 1), fill=tint + (255,))
        top_ink = bottom_ink = config.INK_STALE if dimmed else config.INK_DARK
    elif style == "plain":
        # No tile: the background stays fully transparent and the taskbar
        # shows through. White ink matches ENG/IN; level colour has no band to
        # live in, so it is opt-in via CLAUSAGE_ICON_INK=level.
        if INK_MODE == "level":
            top_ink, bottom_ink = (ink, ink) if dimmed else (top_color, bottom_color)
        else:
            top_ink = bottom_ink = ink
    elif style == "dark":
        draw.rectangle((0, 0, size - 1, size - 1),
                       fill=config.TILE_COLOR + (255,))
        top_ink = bottom_ink = ink
    else:
        draw.rectangle((0, top_box[0], size - 1, top_box[1]),
                       fill=top_color + (255,))
        draw.rectangle((0, bottom_box[0], size - 1, bottom_box[1]),
                       fill=bottom_color + (255,))
        draw.rectangle((0, seam_y, size - 1, seam_y + seam - 1),
                       fill=config.SEAM_COLOR + (255,))
        top_ink = bottom_ink = WHITE

    pad_x = max(1, round(size * PAD_X_RATIO))
    for text, box, ink in ((session_text, top_box, top_ink),
                           (weekly_text, bottom_box, bottom_ink)):
        band_h = box[1] - box[0] + 1

        # Segments first, exactly as the one-row badge does. A thresholded
        # TrueType face at a 10px band height came out ragged, and the two
        # paths looking like different apps was worse than either.
        inner_w = size - 2 * pad_x
        # Each band needs its own top and bottom margin, or the digits sit
        # flush against the tile edge and the badge reads as congested -- the
        # same mistake the one-row badge made before PAD_Y_RATIO existed. Only
        # a pixel here: two rows have little height to spare, and the gutter
        # already separates them from each other.
        band_pad = max(1, round(size * 0.045))
        face, tracking = _fit_segment_spaced(text, inner_w,
                                             band_h - 2 * band_pad)
        if face is not None:
            width = _text_width(face, text, 1, tracking)
            x = pad_x + (inner_w - width) // 2
            y = box[0] + max(0, (band_h - len(face["0"])) // 2)
            _blit(draw, face, text, x, y, ink + (255,), 1, tracking)
            continue

        if family in _TTF_PATHS:
            if _blit_ttf(image, family, text, box, (size, band_h), ink):
                continue
            # No usable face on this machine: fall through to the pixel font.
        font, scale = _fit(text, size, band_h)
        height = _font_height(font) * scale
        x = (size - _text_width(font, text, scale)) // 2
        y = box[0] + max(0, (band_h - height) // 2)
        _blit(draw, font, text, x, y, ink + (255,), scale)

    return image


def render_state(session_pct: float | None, weekly_pct: float | None,
                 dimmed: bool = False,
                 size: int | None = None) -> tuple[Image.Image, tuple]:
    """Render the badge, returning the cache key so callers can skip no-ops."""
    size = size or tray_icon_size()

    if STYLE not in _DIGIT_STYLES:
        shown = badge_window(session_pct, weekly_pct)
        pct = {"five_hour": session_pct, "seven_day": weekly_pct}.get(shown)
        state = theme.state_for(pct)
        key = (badge_text(pct), state, badge_text(pct), state,
               dimmed, size, STYLE, shown or "none", 1)
        # Not named `render`: that is the module-level function the digit
        # styles call further down, and binding it as a local here made
        # Python treat it as local for the whole function -- so every digit
        # style raised UnboundLocalError.
        draw = render_ring if STYLE == "ring" else render_number
        return draw(pct, state, dimmed, size), key

    if ICON_ROWS == 1:
        # With room for only one number, show whichever window is under more
        # pressure -- that is the one worth glancing at.
        candidates = [p for p in (session_pct, weekly_pct) if p is not None]
        worst = max(candidates) if candidates else None
        session_pct = weekly_pct = worst
    key = (badge_text(session_pct), level_for(session_pct),
           badge_text(weekly_pct), level_for(weekly_pct),
           dimmed, size, STYLE, FONT_FAMILY, ICON_ROWS)
    return render(*key), key


# ---------------------------------------------------------------- ring
#
# The badge proper. Supersampled 8x and downsampled, because a ring is all
# curve: drawn at final size directly, a 24px arc is a staircase.

_SS = 8                        # supersample factor
_STALE_TRACK_ALPHA = 60        # a stale ring recedes, track included


def _cap(draw, cx: float, cy: float, radius: float, angle_deg: float,
         width: float, color) -> None:
    """A circle at one end of an arc, standing in for a round line cap.

    Pillow's arc() has butt ends only. At 8x the difference between a butt and
    a round cap is four pixels of stair-stepping that survive the downsample.
    """
    theta = math.radians(angle_deg)
    x = cx + radius * math.cos(theta)
    y = cy + radius * math.sin(theta)
    r = width / 2.0
    draw.ellipse((x - r, y - r, x + r, y + r), fill=color)


def _draw_arc(draw, size: int, margin: float, width: float,
              start_deg: float, sweep_deg: float, color,
              round_caps: bool = True) -> None:
    """Stroke an arc, with circles standing in for round caps.

    The subtlety is where the caps go. Pillow grows an arc's width *inward*
    from the bounding box, so the stroke's centre line sits half a width
    inside the box, not on it. Putting the caps on the box -- which is the
    obvious reading -- pushed them a half-stroke proud of the arc and turned
    every partial ring into a sausage.
    """
    box = (margin, margin, size - 1 - margin, size - 1 - margin)
    draw.arc(box, start_deg, start_deg + sweep_deg, fill=color,
             width=int(round(width)))
    if not round_caps:
        return
    centre = (size - 1) / 2.0
    radius = (size - 1 - 2 * margin) / 2.0 - width / 2.0
    _cap(draw, centre, centre, radius, start_deg, width, color)
    _cap(draw, centre, centre, radius, start_deg + sweep_deg, width, color)


# A sliver of arc so that 1% does not render as 0%. Small enough that it still
# reads as "barely started".
_MIN_SWEEP_DEG = 9.0
_TWELVE = -90.0               # Pillow measures clockwise from 3 o'clock


# The number inside the ring. On by default: the ring alone answers "roughly
# how much", but not "how much", and the answer is the point of the app.
RING_NUMBER = os.environ.get("CLAUSAGE_RING_NUMBER", "1").strip()     not in ("0", "false", "no")

# Semibold reads cleaner than Bold at ten pixels: Bold's stems start to close
# the counters in 8 and 9 once the downsample softens them.
_NUMERAL_WEIGHT = "semibold"


def _ring_hole(n: float, margin: float, width: float) -> float:
    """Diameter of the clear space inside the stroke."""
    return n - 2.0 * margin - 2.0 * width


def _numeral_box(hole: float) -> tuple[int, int]:
    """The largest rectangle that fits inside the hole, as (width, height).

    A circle of diameter d holds a w x h rectangle only while
    w^2 + h^2 <= d^2. Digits are taller than wide, so the split is not
    square: 0.82 of the diameter across and 0.55 down keeps the diagonal
    inside the circle (0.82^2 + 0.55^2 = 0.97) and matches the proportions of
    a two-digit number.
    """
    return int(hole * 0.82), int(hole * 0.55)


@lru_cache(maxsize=256)
def render_ring(pct: float | None, state: str, dimmed: bool,
                size: int, number: bool | None = None) -> Image.Image:
    """The progress ring, at exactly `size` pixels.

    pct None means "no reading": the track alone, fainter still. That is
    deliberately different from 0%, which is a full track and an empty arc --
    "you have used none of it" is a reading, and a useful one.

    The ring and its plate are drawn supersampled and downsampled, because a
    curve rendered at 24px directly is a staircase. The digits are then
    plotted at the *final* size on top: they are axis-aligned rectangles, so
    supersampling them only softens edges that were already exact.
    """
    if number is None:
        number = RING_NUMBER

    # The numeral decides the ring's weight, so it has to be resolved first:
    # a thin ring leaves a hole a number can live in, and a bold one does not.
    # If the number will not fit legibly even then, the ring goes back to bold
    # and bare -- at that point the stroke is the only signal there is.
    text = badge_text(pct) if (number and pct is not None) else ""
    fitted = None
    stroke_r, margin_r = theme.RING_STROKE, theme.RING_MARGIN
    if text:
        hole_px = _ring_hole(size, size * theme.RING_MARGIN_NUMBERED,
                             max(1.0, size * theme.RING_STROKE_NUMBERED))
        box_w, box_h = _numeral_box(hole_px)
        if box_h >= theme.RING_MIN_GLYPH_PX:
            fitted = _fit_face(_NUMERAL_WEIGHT, text, box_w, box_h)
    if fitted is not None:
        stroke_r = theme.RING_STROKE_NUMBERED
        margin_r = theme.RING_MARGIN_NUMBERED

    n = max(8, size) * _SS
    image = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    margin = n * margin_r
    width = max(_SS, n * stroke_r)

    track_alpha = _STALE_TRACK_ALPHA if dimmed else theme.TRAY_TRACK_ALPHA
    if pct is None:
        track_alpha = _STALE_TRACK_ALPHA
    _draw_arc(draw, n, margin, width, 0, 360,
              theme.TRAY_TRACK + (track_alpha,), round_caps=False)

    if pct is not None and pct > 0:
        accent = theme.tray_accent(state, stale=dimmed) + (255,)
        sweep = 360.0 * min(float(pct), 100.0) / 100.0
        sweep = max(_MIN_SWEEP_DEG, sweep)
        # A full ring must not be drawn as a 360-degree arc with caps: the two
        # caps land on the same spot and bulge.
        _draw_arc(draw, n, margin, width, _TWELVE, sweep, accent,
                  round_caps=sweep < 359.0)

    if fitted is not None:
        hole = _ring_hole(n, margin, width)
        radius = hole / 2.0
        centre = (n - 1) / 2.0
        draw.ellipse((centre - radius, centre - radius,
                      centre + radius, centre + radius),
                     fill=theme.RING_PLATE + (theme.RING_PLATE_ALPHA,))

        # Drawn here, inside the supersampled pass, so the glyph edges are
        # antialiased by the same downsample that smooths the arc. The old
        # seven-segment face was plotted at final size because it had to stay
        # crisp on a transparent background; the plate removed that
        # constraint, and a real typeface is the whole point.
        size_pt, _box = fitted
        font = _face(_NUMERAL_WEIGHT, size_pt * _SS)
        if font is not None:
            box = draw.textbbox((0, 0), text, font=font)
            ink = theme.RING_INK_STALE if dimmed else theme.RING_INK
            draw.text(((n - (box[2] - box[0])) / 2.0 - box[0],
                       (n - (box[3] - box[1])) / 2.0 - box[1]),
                      text, font=font, fill=ink + (255,))

    return image.resize((size, size), Image.LANCZOS)


# ---------------------------------------------------------------- number badge
#
# The ring is gone from the tray. It read as a meter, which was the point, but
# it cost two thirds of the slot's diameter: the numeral inside a 24px ring
# gets 9px of height, while the same slot with no ring gives it 13px. At tray
# size that difference is the whole legibility of the thing.
#
# What the ring carried, the plate now carries: state lives in the tint (see
# theme.PLATE_TINTS), so urgency still reads without an arc. The percentage is
# the badge, which is what it should have been at this size all along -- the
# ring belongs on the panel, where there is room for both.


@lru_cache(maxsize=256)
def render_number(pct: float | None, state: str, dimmed: bool,
                  size: int) -> Image.Image:
    """A tinted rounded plate with the percentage set as large as it fits."""
    n = max(8, size) * _SS
    image = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    radius = n * 0.22
    edge = max(_SS, int(round(n * 0.012)))
    draw.rounded_rectangle((0, 0, n - 1, n - 1), radius=radius,
                           fill=theme.plate_tint(state, dimmed) + (255,),
                           outline=theme.plate_edge(state, dimmed) + (255,),
                           width=edge)

    text = badge_text(pct)
    pad = max(1, round(size * theme.PLATE_PAD))
    inner = size - 2 * pad

    # Size against a two-digit reference rather than the actual text, so that
    # 7 and 27 share a glyph height. Fitting each string on its own let a
    # single digit fill the whole plate and then shrink by a third the moment
    # usage crossed 10 -- the badge appeared to change size as the number
    # changed, which reads as a glitch rather than as data.
    reference = "8" * max(2, len(text))
    fitted = _fit_face(_NUMERAL_WEIGHT, reference, inner, inner)
    if fitted is not None:
        points, _box = fitted
        font = _face(_NUMERAL_WEIGHT, points * _SS)
        if font is not None:
            box = draw.textbbox((0, 0), text, font=font)
            ink = theme.RING_INK_STALE if dimmed else theme.RING_INK
            draw.text(((n - (box[2] - box[0])) / 2.0 - box[0],
                       (n - (box[3] - box[1])) / 2.0 - box[1]),
                      text, font=font, fill=ink + (255,))

    return image.resize((size, size), Image.LANCZOS)


# ---------------------------------------------------------------- panel gauge
#
# The same ring, drawn large for the panel. Sharing the arc code is the point:
# it is what makes the tray badge and the panel read as one application rather
# than two that happen to show the same numbers.
#
# At this size the percentage fits inside the ring, which it cannot do at 16px.
# The panel also controls its own background -- a dark card -- so light ink is
# safe here in a way it never is on an unknown taskbar.

# The panel gauge takes its proportions from the tray ring rather than
# inventing its own, so the two read as one object at two sizes. It differs
# only where it must: it can afford a real typeface and a "%" sign, because it
# has four times the diameter to spend.
_GAUGE_SS = 4


@lru_cache(maxsize=128)
def render_gauge(pct: float | None, state: str, dimmed: bool, size: int,
                 numeral: bool = True) -> Image.Image:
    """A ring gauge with the percentage inside it, for the panel."""
    n = max(16, size) * _GAUGE_SS
    image = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    margin = n * theme.RING_MARGIN_NUMBERED
    width = max(_GAUGE_SS, n * theme.RING_STROKE_NUMBERED)

    _draw_arc(draw, n, margin, width, 0, 360,
              theme.TRAY_TRACK + (70 if dimmed else 125,), round_caps=False)

    if numeral:
        # The same backing plate as the tray badge. Here it is not strictly
        # needed -- the panel owns its background -- but leaving it out made
        # the two rings look like different components.
        hole = _ring_hole(n, margin, width)
        radius = hole / 2.0
        centre = (n - 1) / 2.0
        draw.ellipse((centre - radius, centre - radius,
                      centre + radius, centre + radius),
                     fill=theme.RING_PLATE + (theme.RING_PLATE_ALPHA,))

    accent = theme.accent(state, stale=dimmed)
    if pct is not None and pct > 0:
        sweep = max(_MIN_SWEEP_DEG, 360.0 * min(float(pct), 100.0) / 100.0)
        _draw_arc(draw, n, margin, width, _TWELVE, sweep, accent + (255,),
                  round_caps=sweep < 359.0)

    if numeral:
        text = "--" if pct is None else "{0:.0f}%".format(pct)
        inner = _ring_hole(n, margin, width) - n * 0.06
        font = None
        for points in range(int(inner), 5, -2):
            candidate = _ttf("sans", points)
            if candidate is None:
                break
            box = draw.textbbox((0, 0), text, font=candidate)
            # Leave a margin inside the ring: a numeral that touches the arc
            # makes the gauge read as a filled disc with a hole punched in it.
            if (box[2] - box[0] <= inner * 0.90
                    and box[3] - box[1] <= inner * 0.58):
                font = candidate
                break
        if font is not None:
            box = draw.textbbox((0, 0), text, font=font)
            ink = theme.RING_INK_STALE if dimmed else theme.RING_INK
            draw.text(((n - (box[2] - box[0])) / 2 - box[0],
                       (n - (box[3] - box[1])) / 2 - box[1]),
                      text, font=font, fill=ink + (255,))

    return image.resize((size, size), Image.LANCZOS)
