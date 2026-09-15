"""One visual language for all three surfaces.

The tray badge, the tooltip and the panel are three different rendering
technologies -- a 24px Pillow bitmap, a 127-character plain-text field, and a
Tk window -- which is exactly why the palette and the vocabulary have to live
in one place. Before this module each surface had invented its own.

The state bands are display-only. They are deliberately *not* the notification
thresholds in ``config.THRESHOLDS``: alerts fire at 50/75/100 and must keep
doing so, while the eye wants a finer gradation than three steps.
"""

from __future__ import annotations

# ---------------------------------------------------------------- states
#
# Five bands plus "unknown". More than the three notification thresholds,
# because 90% and 76% feel very different and both are "hot" under the old
# scheme; fewer than a continuous gradient, because a colour that means
# something has to be nameable.

CALM = "calm"            # 0-49    nothing to think about
MODERATE = "moderate"    # 50-74   worth knowing
WARNING = "warning"      # 75-89   plan around it
HIGH = "high"            # 90-99   nearly out
CRITICAL = "critical"    # 100+    out
UNKNOWN = "unknown"      # no reading

STATE_ORDER = (CALM, MODERATE, WARNING, HIGH, CRITICAL)

# Upper bound of each band, exclusive.
_BANDS = ((50.0, CALM), (75.0, MODERATE), (90.0, WARNING), (100.0, HIGH))


def state_for(pct: float | None) -> str:
    """The display state for a percentage."""
    if pct is None:
        return UNKNOWN
    for limit, name in _BANDS:
        if pct < limit:
            return name
    return CRITICAL


# ---------------------------------------------------------------- accents
#
# One accent per state, used for the tray arc, the tooltip's wording and the
# panel's rings and bars. Low usage reads green, not neutral grey: "you have
# room" is itself information, and a grey ring at 5% looks broken rather than
# calm.

ACCENT = {
    CALM: (52, 178, 96),        # green
    MODERATE: (235, 168, 30),   # amber
    WARNING: (243, 129, 46),    # orange
    HIGH: (243, 79, 66),        # red-orange
    CRITICAL: (214, 45, 42),    # red
    UNKNOWN: (122, 131, 143),   # grey-blue
}

# What a stale reading fades toward. Blending toward this grey-blue keeps a
# hint of the hue -- a stale 90% still leans red -- without the muddy olive a
# hand-picked "desaturated amber" turned out to be.
STALE_TOWARD = (122, 130, 142)
STALE_MIX = 0.70            # how far toward the grey; 1.0 would be flat grey


def _blend(a, b, t):
    return tuple(int(round(a[i] * (1 - t) + b[i] * t)) for i in range(3))


def accent(state: str, stale: bool = False) -> tuple[int, int, int]:
    base = ACCENT.get(state, ACCENT[UNKNOWN])
    if not stale:
        return base
    return _blend(base, STALE_TOWARD, STALE_MIX)


def tray_accent(state: str, stale: bool = False) -> tuple[int, int, int]:
    """As accent(), but the calm band is cool grey rather than green.

    See TRAY_CALM. Every other state is identical to the panel's, so the two
    still read as one colour system where the colour actually means something.
    """
    if state == CALM:
        base = TRAY_CALM
        return _blend(base, STALE_TOWARD, STALE_MIX) if stale else base
    return accent(state, stale)


# ---------------------------------------------------------------- tray
#
# The badge is a single bitmap that has to sit on either a dark or a light
# taskbar, and Windows tells us nothing about which. So the track is a
# mid-tone: dark enough to read on white, light enough to read on near-black.
# Everything else is the accent, which is saturated enough to survive both.

# The tray's own take on the calm state. The panel shows low usage as green,
# because "you have room" is worth saying in a dashboard. In the taskbar that
# same green is a small bright dot competing with everything else for
# attention, and a usage monitor at 12% has nothing to say. So below 50% the
# ring goes cool grey and only starts colouring when there is a reason to.
#
# (The brief asks for both -- "0-49% neutral / cool gray" for the tray in one
# section and "green represents healthy" for the panel in another. This is how
# both are true at once.)
# A blue-grey rather than a neutral one: against a bright plate a neutral grey
# ring reads as an unfinished shape, while a slate blue reads as a colour that
# was chosen.
TRAY_CALM = (108, 128, 158)

TRAY_TRACK = (128, 136, 148)
TRAY_TRACK_ALPHA = 110          # a hint of a track, not a grey donut
TRAY_INK = (236, 240, 245)      # the numeral, when there is room for one
TRAY_INK_DARK = (22, 24, 28)

# Fraction of the icon's width taken by the ring stroke.
#
# A stroke above ~0.20 closes the hole and the ring reads as a filled dot;
# below ~0.06 it stops reading as a ring at all.
RING_STROKE = 0.20
RING_MARGIN = 0.02              # the slot is small; barely any is wasted

# When a numeral is drawn the ring only has to *hint* at the value, because
# the digits state it. So it gets out of the way: a thinner stroke and a
# tighter margin, which is where the numeral's room comes from. A bare ring is
# the only signal there is, so it stays bold.
RING_STROKE_NUMBERED = 0.135
RING_MARGIN_NUMBERED = 0.0

# The numeral's own backing plate. This is what makes a number inside the ring
# survive an unknown taskbar: white ink alone vanishes against a light one, so
# the ink gets a dark disc of its own to sit on. On a dark taskbar the plate is
# all but invisible and the digits look like they float inside the ring; on a
# light one it reads as a deliberate dark centre.
# A *bright* plate with dark ink on it, rather than the reverse.
#
# The first version was a dark disc with light digits, which works on a dark
# taskbar and only just works on a light one. Inverting it is better on both
# counts: a bright disc is the strongest silhouette a 24px icon can have
# against either taskbar, and near-black on near-white is the highest-contrast
# pairing there is -- about 17:1, against 4.9:1 for the light-on-dark version.
RING_PLATE = (242, 244, 248)
RING_PLATE_ALPHA = 255
RING_INK = (24, 26, 32)         # the numeral, on the bright plate
RING_INK_STALE = (128, 136, 148)

# Below this many pixels of glyph height a numeral is worse than nothing -- it
# reads as a smudge and makes the ring look dirty. Then the ring goes bare.
RING_MIN_GLYPH_PX = 7

# ---------------------------------------------------------------- number badge
#
# With no ring there is no arc to carry the state, so the plate itself is
# tinted. Pale rather than saturated: the badge has to stay a quiet block of
# colour in a taskbar, and dark ink needs a light ground to sit on. Every one
# of these clears 12:1 against RING_INK, so the number never gets harder to
# read as the state changes -- only the mood shifts.
PLATE_TINTS = {
    CALM: (236, 240, 246),        # near-white, faintly cool
    MODERATE: (253, 240, 206),    # pale amber
    WARNING: (253, 228, 205),     # pale orange
    HIGH: (253, 214, 211),        # pale red
    CRITICAL: (250, 189, 186),    # deeper red; the one state allowed to shout
    UNKNOWN: (226, 230, 236),     # pale grey
}

# A hairline a shade darker than the tint, so the badge still has an edge on a
# light taskbar where the plate itself nearly vanishes.
PLATE_EDGE_MIX = 0.22

# Padding inside the badge, as a fraction of its size. Two pixels at 24px:
# enough that the digits do not touch the corner radius, and no more -- every
# pixel spent here is a pixel off the numeral.
PLATE_PAD = 0.08


def plate_tint(state: str, stale: bool = False) -> tuple[int, int, int]:
    tint = PLATE_TINTS.get(state, PLATE_TINTS[UNKNOWN])
    if stale:
        return _blend(tint, PLATE_TINTS[UNKNOWN], 0.75)
    return tint


def plate_edge(state: str, stale: bool = False) -> tuple[int, int, int]:
    return _blend(plate_tint(state, stale), (60, 66, 78), PLATE_EDGE_MIX)

# ---------------------------------------------------------------- panel

PANEL_BG = "#191a1f"            # the window itself
PANEL_SURFACE = "#20222a"       # a row's card
PANEL_SURFACE_ALT = "#252831"   # hover / emphasis
PANEL_BORDER = "#2c3039"        # hairline separators
PANEL_TRACK = "#343945"         # the unfilled part of a bar

TEXT = "#eceff5"                # primary
TEXT_MUTED = "#9aa3b2"          # secondary: resets, sources
TEXT_FAINT = "#6e7681"          # tertiary: the footer
CLAUDE_CORAL = "#d97757"        # the mark, and nothing else

FONT_UI = "Segoe UI"
FONT_UI_SIZE = 10
FONT_NUM = "Segoe UI"           # tabular by default in Segoe's numerals

# TrueType files, for the surfaces that rasterise their own text (the tray
# badge and the hover card). Ordered best-first; the first that exists wins.
# No monospace anywhere in the visual surfaces -- that is what made the old
# tooltip read as console output.
FONT_FILES = {
    "bold": ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    "semibold": ("C:/Windows/Fonts/seguisb.ttf",
                 "C:/Windows/Fonts/segoeuib.ttf",
                 "C:/Windows/Fonts/arialbd.ttf"),
    "regular": ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf"),
}

# ---------------------------------------------------------------- hover card
#
# A layered window drawn with Pillow, because the native tray tooltip is a
# fixed 127-character plain-text field and can never be made to look like a
# product. Same palette as the panel, one step darker so it reads as a
# floating surface rather than a second window.

CARD_BG = (26, 27, 32)
CARD_BORDER = (58, 62, 72)
CARD_RADIUS = 10
CARD_SHADOW_ALPHA = 46          # a hint of depth, not a drop shadow
CARD_TRACK = (52, 57, 68)


def hex_of(rgb: tuple[int, int, int]) -> str:
    return "#{0:02x}{1:02x}{2:02x}".format(*rgb)


def rgb_of(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


# Tk wants "#rrggbb"; Pillow wants a tuple. Derived rather than typed twice,
# so the panel and the hover card cannot end up a shade apart.
TEXT_RGB = rgb_of(TEXT)
TEXT_MUTED_RGB = rgb_of(TEXT_MUTED)
TEXT_FAINT_RGB = rgb_of(TEXT_FAINT)
CLAUDE_CORAL_RGB = rgb_of(CLAUDE_CORAL)
PANEL_BG_RGB = rgb_of(PANEL_BG)
PANEL_SURFACE_RGB = rgb_of(PANEL_SURFACE)


def accent_hex(state: str, stale: bool = False) -> str:
    return hex_of(accent(state, stale))


# ---------------------------------------------------------------- formatting

_CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£",
                     "JPY": "¥", "INR": "₹"}


def money(amount: float, currency: str, decimals: int = 2) -> str:
    """"$34.00", falling back to "CHF 34.00" for anything unmapped."""
    symbol = _CURRENCY_SYMBOLS.get((currency or "").upper())
    body = "{0:,.{1}f}".format(amount, decimals)
    if symbol:
        return symbol + body
    return "{0} {1}".format(currency or "", body).strip()


def join_thresholds(crossed) -> str:
    """"50%", "50% and 75%", "50%, 75% and 100%"."""
    labels = ["{0}%".format(t) for t in crossed]
    if len(labels) == 1:
        return labels[0]
    return "{0} and {1}".format(", ".join(labels[:-1]), labels[-1])


# ---------------------------------------------------------------- wording
#
# Shared so the three surfaces cannot drift apart. "Session" in the tooltip
# and "Current session" in the panel is deliberate -- the tooltip is paying
# 127 characters for every word -- but they come from one table.

SHORT_LABELS = {
    "five_hour": "Session",
    "seven_day": "Weekly",
    "credits": "Credits",
}

LONG_LABELS = {
    "five_hour": "Current session",
    "seven_day": "Weekly, all models",
    "seven_day_opus": "Weekly, Opus",
    "seven_day_sonnet": "Weekly, Sonnet",
    "credits": "Usage credits",
}
