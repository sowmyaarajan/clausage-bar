"""The click-through usage panel.

A tray tooltip is plain text in a 128-character field, so it can never be made
to look good. This is where the visual detail lives: a small dark window laid
out like Claude's own usage view, with real progress bars and reset times.

It runs as its own process (``pythonw -m clausage_bar.panel``) rather than a
thread. Tk demands that every call happen on the thread that created the
interpreter, and pystray's callbacks do not guarantee that; a separate process
sidesteps the whole problem and cannot take the tray down with it.

Each row is drawn on a single Tk canvas rather than assembled from packed
widgets. Tk frames cannot have rounded corners, and a column of hard-edged
rectangles is what made the old layout read as a form rather than as a status
panel. One canvas per row also puts the ring, the label, the percentage and
the bar on a single coordinate system, which is the only way their baselines
line up reliably.

The ring on each row is the same renderer as the tray badge (``icon.py``),
drawn large. That is deliberate: it is the thing that makes the badge, the
tooltip and this window read as one application.
"""

from __future__ import annotations

import ctypes
import os
import sys
import tkinter as tk
from datetime import datetime, timezone

from . import config, freshness, icon as icon_mod, theme
from .atomicjson import read_json, write_atomic
from .model import Snapshot

FONT = theme.FONT_UI
REFRESH_REQUEST = config.CLAUSAGE_DIR / "refresh.request"


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


MONITOR_DEFAULTTONEAREST = 2
MDT_EFFECTIVE_DPI = 0


def active_monitor() -> tuple[float, tuple[int, int, int, int] | None]:
    """The scale factor and work area of the monitor under the cursor.

    Both halves of this exist because the panel used to assume one screen:

      * The scale came from ``GetDpiForSystem()``, which is the *primary*
        monitor's DPI, fixed for the session. Declaring per-monitor awareness
        and then asking for the system DPI is a contradiction -- dock a laptop
        to a monitor scaled differently and every dimension was computed for
        the wrong screen.
      * The position came from ``winfo_screenwidth/height``, the primary
        screen's size, so the panel opened bottom-right of the *primary*
        display no matter which monitor the tray icon was clicked on.

    The cursor is the right thing to ask: the panel only ever opens because
    someone just clicked the tray icon, so the pointer is on the intended
    monitor. Asking before any window exists also means the answer is
    available in time to lay out with, which ``GetDpiForWindow`` would not be.

    The work area excludes the taskbar, which is strictly better than the
    fixed 60px gap the old placement guessed at -- that was wrong for a
    taskbar on the side, or an unusually tall one.

    Returns ``(1.0, None)`` on anything unexpected, which reproduces the old
    single-screen behaviour rather than failing.
    """
    try:
        user32 = ctypes.windll.user32
        point = _POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return 1.0, None
        handle = user32.MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST)
        if not handle:
            return 1.0, None

        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        work = None
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            work = (info.rcWork.left, info.rcWork.top,
                    info.rcWork.right, info.rcWork.bottom)

        # GetDpiForMonitor lives in shcore, and only from Windows 8.1.
        dpi_x = ctypes.c_uint()
        dpi_y = ctypes.c_uint()
        try:
            shcore = ctypes.windll.shcore
            if shcore.GetDpiForMonitor(handle, MDT_EFFECTIVE_DPI,
                                       ctypes.byref(dpi_x),
                                       ctypes.byref(dpi_y)) == 0:
                return max(1.0, dpi_x.value / 96.0), work
        except (AttributeError, OSError):
            pass
        return max(1.0, user32.GetDpiForSystem() / 96.0), work
    except (AttributeError, OSError):
        return 1.0, None


def declare_dpi_aware() -> float:
    """Opt out of DPI virtualisation and return the display's scale factor.

    This is why the panel looked blurry. The tray process declares per-monitor
    awareness, but the panel is a *separate* process and declared nothing -- so
    Windows told it the screen was 1280x720, Tk laid out a 452px window in
    those coordinates, and the compositor stretched the result by 1.5 to fill
    1920x1080. Every glyph and every rounded corner went through a bilinear
    upscale.

    Declaring awareness fixes the sharpness but shrinks everything to two
    thirds, so every dimension and font size is multiplied by the factor
    returned here. That is the whole trade: Windows will either scale the
    pixels for you badly, or hand you the real ones and expect you to do the
    arithmetic.

    Must happen before Tk creates a window; awareness cannot be changed after.
    """
    try:
        user32 = ctypes.windll.user32
        # -4 == DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if not user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return 1.0
        return max(1.0, user32.GetDpiForSystem() / 96.0)
    except (AttributeError, OSError):
        return 1.0

# Windows whose row is shown when the endpoint reports them.
ROWS = ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet")


def reset_phrase(eff, now: datetime) -> str:
    if eff.inferred:
        return "Window reset — showing 0%"
    if eff.next_reset is None:
        return "Reset time unknown"
    local = eff.next_reset.astimezone()
    remaining = freshness.fmt_until(eff.next_reset, now)
    when = local.strftime("%I:%M %p").lstrip("0")
    if local.date() != now.astimezone().date():
        when = local.strftime("%a ") + when
    if remaining:
        return "Resets {0}   ·   in {1}".format(when, humanize(remaining))
    return "Resets {0}".format(when)


def humanize(short: str) -> str:
    """'4h24m' -> '4 hr 24 min';  '6d' -> '6 days';  '45s' -> '45 sec'."""
    if short.endswith("d"):
        days = short[:-1]
        return "{0} day{1}".format(days, "" if days == "1" else "s")
    if short.endswith("s") and "h" not in short and "m" not in short:
        return short[:-1] + " sec"
    return (short.replace("h", " hr ").replace("m", " min").strip())


class Panel:
    # Design sizes, in the 96-dpi units this layout was drawn at. Each is
    # multiplied by the display scale in __init__, so the panel keeps the same
    # apparent size at 100% and 150% while being rendered at real pixels.
    WIDTH = 452
    PAD = 20                 # window margin
    CARD_PAD = 14            # inside a row
    GAUGE = 52
    ROW_H = 84
    ROW_GAP = 8
    BAR_H = 7
    RADIUS = 10

    def __init__(self) -> None:
        # Awareness first and once: it cannot be changed after a window
        # exists, and the monitor query below is only meaningful once the
        # process is being told the truth about the display.
        declare_dpi_aware()
        monitor_scale, self.work_area = active_monitor()
        self.scale = monitor_scale * config.PANEL_SCALE
        for name in ("WIDTH", "PAD", "CARD_PAD", "GAUGE", "ROW_H", "ROW_GAP",
                     "BAR_H", "RADIUS"):
            setattr(self, name, self.px(getattr(Panel, name)))

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Claude usage")
        self.root.configure(bg=theme.PANEL_BG)
        self.root.overrideredirect(True)      # no title bar; we draw our own
        self.root.attributes("-topmost", True)

        self.body = tk.Frame(self.root, bg=theme.PANEL_BG,
                             highlightbackground=theme.PANEL_BORDER,
                             highlightthickness=1)
        self.body.pack(fill="both", expand=True)

        self.root.bind("<Escape>", lambda _e: self.close())
        self._closing = False
        # Tk drops an image the moment nothing references it, and a garbage
        # collected PhotoImage renders as an empty box.
        self._images: list = []

        # CLAUSAGE_PANEL_SHOT keeps the window up long enough to screenshot it
        # and writes the image there, for verifying layout changes.
        self._shot_path = os.environ.get("CLAUSAGE_PANEL_SHOT")
        if not self._shot_path:
            self.root.bind("<FocusOut>", self._maybe_close)

        self.render()
        self.place()
        self.root.deiconify()
        self.root.focus_force()

        if self._shot_path:
            self.root.after(900, self._screenshot)

    # ---------------------------------------------------------------- pieces

    def px(self, value: float) -> int:
        """A design dimension in real device pixels."""
        return max(1, int(round(value * self.scale)))

    def font(self, points: float, bold: bool = False):
        """A font tuple sized in *pixels*, not points.

        Tk's point sizes go through its own idea of the display dpi, which on
        Windows is 96 whatever the real value is -- so points would silently
        ignore the scaling this class exists to apply. A negative size means
        pixels, which is deterministic.
        """
        pixels = -self.px(points * 96.0 / 72.0)
        return (FONT, pixels, "bold" if bold else "normal")

    def _label(self, parent, text, *, size=9, color=None, bold=False, **kw):
        return tk.Label(parent, text=text, bg=kw.pop("bg", theme.PANEL_BG),
                        fg=color or theme.TEXT,
                        font=self.font(size, bold),
                        anchor="w", **kw)

    @staticmethod
    def _round_rect(canvas, x0, y0, x1, y1, radius, **kw):
        """A rounded rectangle, which Tk's canvas does not provide."""
        radius = max(0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
        if radius <= 0:
            return canvas.create_rectangle(x0, y0, x1, y1, width=0, **kw)
        points = [
            x0 + radius, y0, x1 - radius, y0, x1, y0,
            x1, y0 + radius, x1, y1 - radius, x1, y1,
            x1 - radius, y1, x0 + radius, y1, x0, y1,
            x0, y1 - radius, x0, y0 + radius, x0, y0,
        ]
        return canvas.create_polygon(points, smooth=True, splinesteps=24,
                                     width=0, **kw)

    def _meter(self, canvas, x0, y, x1, pct, accent_hex):
        """A rounded track with a rounded fill, drawn on the row's canvas."""
        h = self.BAR_H
        r = h / 2.0
        self._round_rect(canvas, x0, y, x1, y + h, r, fill=theme.PANEL_TRACK)
        if pct is None or pct <= 0:
            return
        span = (x1 - x0) * min(float(pct), 100.0) / 100.0
        # A rounded fill narrower than its own diameter degenerates into a
        # lens shape, so anything visible gets at least one full cap width.
        span = max(h, span)
        self._round_rect(canvas, x0, y, x0 + span, y + h, r, fill=accent_hex)

    def _row(self, parent, label: str, pct: float | None, sub: str,
             stale: bool, in_tray: bool = False) -> None:
        state = theme.state_for(pct)
        accent = theme.accent_hex(state, stale)
        inner_w = self.WIDTH - 2 * self.PAD

        canvas = tk.Canvas(parent, width=inner_w, height=self.ROW_H,
                           bg=theme.PANEL_BG, highlightthickness=0, bd=0)
        canvas.pack(fill="x", pady=(0, self.ROW_GAP))

        self._round_rect(canvas, 0, 0, inner_w - 1, self.ROW_H - 1,
                         self.RADIUS, fill=theme.PANEL_SURFACE)

        if in_tray:
            # An accent strip down the left edge, marking the row the tray
            # badge is showing. Without it the panel lists three percentages
            # and leaves the user to work out which one is in the taskbar.
            self._round_rect(canvas, 0, self.px(10), self.px(3),
                             self.ROW_H - self.px(11), self.px(1.5),
                             fill=accent)

        # self.GAUGE is already in device pixels, so the ring is drawn
        # at its real size rather than scaled up by Tk afterwards.
        gauge = icon_mod.render_gauge(pct, state, stale, self.GAUGE)
        photo = self._photo(gauge)
        if photo is not None:
            self._images.append(photo)
            canvas.create_image(self.CARD_PAD + self.GAUGE // 2,
                                self.ROW_H // 2, image=photo)

        text_x = self.CARD_PAD + self.GAUGE + self.px(16)
        right_x = inner_w - self.CARD_PAD

        label_id = canvas.create_text(
            text_x, self.px(24), text=label, anchor="w",
            fill=theme.TEXT_MUTED if stale else theme.TEXT,
            font=self.font(10, bold=True))
        if in_tray:
            # Spelled out, because a coloured strip alone is a riddle.
            end = canvas.bbox(label_id)[2]
            canvas.create_text(end + self.px(8), self.px(25),
                               text="in the tray", anchor="w",
                               fill=theme.TEXT_FAINT, font=self.font(8))
        # The percentage sits on the same baseline as the label and on the
        # same right edge in every row: that shared edge is what lets the
        # three numbers be compared without reading the labels.
        canvas.create_text(
            right_x, self.px(24),
            text="--" if pct is None else "{0:.0f}% used".format(pct),
            anchor="e", fill=accent, font=self.font(10, bold=True))
        canvas.create_text(text_x, self.px(44), text=sub, anchor="w",
                           fill=theme.TEXT_MUTED, font=self.font(8))

        self._meter(canvas, text_x, self.ROW_H - self.px(26), right_x, pct,
                    accent)

    @staticmethod
    def _photo(image):
        try:
            from PIL import ImageTk
            return ImageTk.PhotoImage(image)
        except Exception:
            return None      # no ImageTk: the row still renders, minus its ring

    # ---------------------------------------------------------------- render

    def render(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        self._images.clear()

        snapshot = Snapshot.from_json(read_json(config.SNAPSHOT_FILE))
        now = datetime.now(timezone.utc)
        effs, health, age = freshness.evaluate(snapshot, now)
        stale = health == freshness.STALE
        pad = self.PAD

        # ---- header ----
        head = tk.Frame(self.body, bg=theme.PANEL_BG)
        head.pack(fill="x", padx=pad, pady=(pad, self.px(14)))
        self._label(head, "✳", size=13, color=theme.CLAUDE_CORAL,
                    bold=True).pack(side="left", padx=(0, self.px(8)))
        self._label(head, "Your usage limits", size=13,
                    bold=True).pack(side="left")

        # The team, not the plan tier. On a team seat the limits belong to the
        # organisation, so "Acme Corp" answers "whose quota is this?" in a way
        # that "Team Standard" never did. The tier follows it, quieter, for
        # the cases where the distinction matters.
        account = snapshot.account if snapshot else {}
        org = account.get("org_name") or ""
        tier = (account.get("seat_tier") or "").replace("_", " ").title()
        if org:
            self._label(head, "   " + org, size=10, bold=True,
                        color=theme.TEXT_MUTED).pack(
                            side="left", pady=(self.px(3), 0))
            if tier:
                self._label(head, "  ·  " + tier, size=9,
                            color=theme.TEXT_FAINT).pack(
                                side="left", pady=(self.px(4), 0))
        elif tier:
            self._label(head, "  " + tier, size=9,
                        color=theme.TEXT_MUTED).pack(
                            side="left", pady=(self.px(3), 0))
        close = self._label(head, "✕", size=11, color=theme.TEXT_FAINT,
                            cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e: self.close())
        close.bind("<Enter>", lambda _e: close.configure(fg=theme.TEXT))
        close.bind("<Leave>", lambda _e: close.configure(fg=theme.TEXT_FAINT))

        if snapshot is None:
            self._label(self.body, "No reading yet.",
                        color=theme.TEXT_MUTED).pack(fill="x", padx=pad,
                                                     pady=(4, 4))
            self._label(self.body,
                        "Open Claude Code once, or check clausage.log.",
                        size=8, color=theme.TEXT_FAINT).pack(
                            fill="x", padx=pad, pady=(0, pad))
            return

        rows = tk.Frame(self.body, bg=theme.PANEL_BG)
        rows.pack(fill="x", padx=pad)

        session, weekly = effs.get("five_hour"), effs.get("seven_day")
        shown = icon_mod.badge_window(session.pct if session else None,
                                      weekly.pct if weekly else None)

        for name in ROWS:
            eff = effs.get(name)
            if eff is None or eff.pct is None:
                continue
            self._row(rows, theme.LONG_LABELS[name], eff.pct,
                      reset_phrase(eff, now), stale, in_tray=name == shown)

        spend = snapshot.spend
        if spend:
            sub = "Covers you past your plan limits"
            if spend.used_amount is not None and spend.limit_amount is not None:
                sub = "{0} of {1}".format(
                    theme.money(spend.used_amount, spend.currency),
                    theme.money(spend.limit_amount, spend.currency))
            if not spend.enabled:
                sub += "   ·   disabled"
            elif spend.limit_reached:
                sub += "   ·   limit reached"
            self._row(rows, theme.LONG_LABELS["credits"], spend.percent,
                      sub, stale)

        # ---- footer: one hairline, then status left and action right ----
        rule = tk.Frame(self.body, bg=theme.PANEL_BORDER,
                        height=max(1, self.px(1)))
        rule.pack(fill="x", padx=pad, pady=(self.px(6), 0))

        foot = tk.Frame(self.body, bg=theme.PANEL_BG)
        foot.pack(fill="x", padx=pad, pady=(self.px(12), pad))

        # A dot carries freshness, so the footer does not have to shout it.
        dot_state = theme.UNKNOWN if stale else theme.CALM
        self._label(foot, "●", size=8,
                    color=theme.accent_hex(dot_state, stale)).pack(
                        side="left", padx=(0, self.px(6)))
        status = ("Last read {0} ago  ·  not current".format(
                      freshness.fmt_ago(age))
                  if stale else
                  "Updated {0} ago  ·  via {1}".format(
                      freshness.fmt_ago(age), snapshot.source))
        self._label(foot, status, size=8,
                    color=theme.TEXT_MUTED if stale else theme.TEXT_FAINT
                    ).pack(side="left")

        refresh = self._label(foot, "↻  Refresh", size=9,
                              color=theme.TEXT_MUTED, cursor="hand2")
        refresh.pack(side="right")
        refresh.bind("<Button-1>", lambda _e: self.refresh())
        refresh.bind("<Enter>", lambda _e: refresh.configure(fg=theme.TEXT))
        refresh.bind("<Leave>",
                     lambda _e: refresh.configure(fg=theme.TEXT_MUTED))

    def place(self) -> None:
        """Bottom-right of the monitor the user is actually looking at.

        The work area already excludes the taskbar wherever it is docked, so
        no gap has to be guessed at. Falls back to the primary screen when the
        monitor query came back empty, which is the behaviour this had before.
        """
        self.root.update_idletasks()
        width = self.WIDTH
        height = self.root.winfo_reqheight()

        if self.work_area:
            left, top, right, bottom = self.work_area
            margin = self.px(12)
            x = max(left + self.px(8), right - width - margin)
            y = max(top + self.px(8), bottom - height - margin)
        else:
            x = max(8, self.root.winfo_screenwidth() - width - 12)
            y = max(8, self.root.winfo_screenheight() - height - 60)

        self.root.geometry("{0}x{1}+{2}+{3}".format(width, height, x, y))

    # ---------------------------------------------------------------- actions

    def refresh(self) -> None:
        """Ask the tray to poll now, then re-render when its snapshot changes."""
        before = (read_json(config.SNAPSHOT_FILE) or {}).get("captured_at")
        write_atomic(REFRESH_REQUEST,
                     {"requested_at": datetime.now(timezone.utc).isoformat()})
        self._await_new(before, attempts=40)

    def _await_new(self, before, attempts: int) -> None:
        current = (read_json(config.SNAPSHOT_FILE) or {}).get("captured_at")
        if current != before or attempts <= 0:
            self.render()
            self.place()
            return
        self.root.after(250, lambda: self._await_new(before, attempts - 1))

    def _screenshot(self) -> None:
        """Debug helper: save the window as a PNG, then exit.

        Uses PrintWindow rather than a screen grab: an overrideredirect window
        does not reliably raise above a maximized one, and a screen grab would
        then capture whatever is behind it.
        """
        try:
            import ctypes
            from ctypes import wintypes
            from PIL import Image

            self.root.update_idletasks()
            hwnd = self.root.winfo_id()
            user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32

            # Tk may hand back a child HWND; walk up to the real toplevel.
            for _ in range(4):
                parent = user32.GetParent(hwnd)
                if not parent:
                    break
                hwnd = parent

            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            width = rect.right - rect.left
            height = rect.bottom - rect.top

            src = user32.GetWindowDC(hwnd)
            dst = gdi32.CreateCompatibleDC(src)
            bitmap = gdi32.CreateCompatibleBitmap(src, width, height)
            gdi32.SelectObject(dst, bitmap)
            # 2 == PW_RENDERFULLCONTENT
            user32.PrintWindow(hwnd, dst, 2)

            buffer = ctypes.create_string_buffer(width * height * 4)

            class BMI(ctypes.Structure):
                _fields_ = [("biSize", wintypes.DWORD),
                            ("biWidth", wintypes.LONG),
                            ("biHeight", wintypes.LONG),
                            ("biPlanes", wintypes.WORD),
                            ("biBitCount", wintypes.WORD),
                            ("biCompression", wintypes.DWORD),
                            ("biSizeImage", wintypes.DWORD),
                            ("biXPelsPerMeter", wintypes.LONG),
                            ("biYPelsPerMeter", wintypes.LONG),
                            ("biClrUsed", wintypes.DWORD),
                            ("biClrImportant", wintypes.DWORD)]
            info = BMI()
            info.biSize = ctypes.sizeof(BMI)
            info.biWidth = width
            info.biHeight = -height          # negative: top-down rows
            info.biPlanes = 1
            info.biBitCount = 32
            gdi32.GetDIBits(dst, bitmap, 0, height, buffer,
                            ctypes.byref(info), 0)

            Image.frombuffer("RGB", (width, height), buffer,
                             "raw", "BGRX", 0, 1).save(self._shot_path)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(dst)
            user32.ReleaseDC(hwnd, src)
            print("wrote {0} ({1}x{2})".format(self._shot_path, width, height))
        except Exception as exc:
            print("screenshot failed:", exc)
        self.close()

    def _maybe_close(self, _event) -> None:
        # Clicking a child widget briefly moves focus; only close on a real
        # loss of focus to another window.
        self.root.after(120, self._close_if_unfocused)

    def _close_if_unfocused(self) -> None:
        try:
            if not self.root.focus_displayof():
                self.close()
        except (tk.TclError, KeyError):
            self.close()

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> int:
        self.root.mainloop()
        return 0


def main() -> int:
    config.ensure_dirs()
    try:
        return Panel().run()
    except Exception:
        from .logging_setup import get, setup
        setup()
        get("panel").exception("panel failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
