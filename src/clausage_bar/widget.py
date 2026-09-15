"""A floating two-line usage indicator, sized like the IME "ENG / IN" button.

Why this exists: Windows gives a tray icon exactly SM_CXSMICON pixels -- 16x16
at 100% display scaling -- and no application can enlarge its own slot. The IME
language indicator this imitates is not a tray icon at all; it is a taskbar
button with roughly 24px of height and ~8.7px glyphs, measured off a real
screenshot. A tray icon cannot reach that size at 100% scaling.

So this is a borderless always-on-top window that parks next to the tray. Not
being bound by the 16px slot, it can use real Segoe UI at whatever size reads
comfortably, with proper leading between the two rows.

Left-click opens the panel, drag moves it (the position is remembered),
right-click or Escape hides it.

Run it with:  pythonw -m clausage_bar.widget
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from datetime import datetime, timezone

from . import config, freshness, theme
from .atomicjson import read_json, write_atomic
from .model import Snapshot

BG = "#1b1c20"          # close to the Windows 11 dark taskbar
TEXT = theme.TEXT
MUTED = theme.TEXT_MUTED

# Times New Roman has room to be itself here: at ~17px the serifs actually
# render, unlike the tray icon's 7px rows where they only thin the strokes.
FONT = os.environ.get("CLAUSAGE_WIDGET_FONT", "Times New Roman")
POSITION_FILE = config.CLAUSAGE_DIR / "widget.json"
CREATE_NO_WINDOW = 0x08000000


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


# ENG/IN measures ~21x24px with ~8.7px glyphs. 13pt bold Times New Roman is
# ~17px tall -- roughly double -- and lands the widget near 60x48.
FONT_SIZE = _env_int("CLAUSAGE_WIDGET_FONT_SIZE", 13)
REFRESH_S = _env_int("CLAUSAGE_WIDGET_REFRESH_S", 5)
ACCENT_W = 3            # colored bar per row, so white text can still show level
ROW_GAP = _env_int("CLAUSAGE_WIDGET_ROW_GAP", 2)   # leading between the rows


def level_of(pct: float | None) -> str:
    """The shared five-band display state, so this cannot drift from the rest."""
    return theme.state_for(pct)


def format_pct(pct: float | None) -> str:
    if pct is None:
        return "--"
    return "{0:.0f}%".format(pct)


class Widget:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Claude usage")
        self.root.overrideredirect(True)     # no chrome, no alt-tab entry
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)

        # The taskbar is ~48px tall at 100% scaling, so the whole widget has to
        # stay under that to sit inside it. Every pad here is deliberate:
        # Tk labels carry generous default padding that would push it to ~62px.
        self.frame = tk.Frame(self.root, bg=BG, padx=6, pady=1)
        self.frame.pack(fill="both", expand=True)

        self.rows = []
        for index in range(2):
            row = tk.Frame(self.frame, bg=BG)
            row.pack(fill="x", pady=(0, ROW_GAP) if index == 0 else 0)
            accent = tk.Frame(row, bg=BG, width=ACCENT_W)
            accent.pack(side="left", fill="y", padx=(0, 5))
            label = tk.Label(row, text="--", bg=BG, fg=TEXT,
                             font=(FONT, FONT_SIZE, "bold"), anchor="e", width=4,
                             padx=0, pady=0, bd=0, highlightthickness=0)
            label.pack(side="left")
            self.rows.append((accent, label))

        self._drag = None
        self._moved = False
        self._closing = False

        targets = [self.root, self.frame]
        targets += [label for _accent, label in self.rows]
        for target in targets:
            target.bind("<Button-1>", self._press)
            target.bind("<B1-Motion>", self._drag_move)
            target.bind("<ButtonRelease-1>", self._release)
            target.bind("<Button-3>", lambda _e: self.close())
        self.root.bind("<Escape>", lambda _e: self.close())

        self.refresh()
        self.restore_position()
        self.root.deiconify()
        self.root.after(REFRESH_S * 1000, self._tick)

    # ---------------------------------------------------------------- data

    def refresh(self) -> None:
        snapshot = Snapshot.from_json(read_json(config.SNAPSHOT_FILE))
        now = datetime.now(timezone.utc)
        effs, health, age = freshness.evaluate(snapshot, now)
        stale = snapshot is not None and health != freshness.FRESH

        for (accent, label), name in zip(self.rows, ("five_hour", "seven_day")):
            eff = effs.get(name)
            pct = eff.pct if eff else None
            accent.configure(bg=theme.accent_hex(level_of(pct), stale))
            label.configure(text=format_pct(pct), fg=MUTED if stale else TEXT)

    def _tick(self) -> None:
        if self._closing:
            return
        try:
            self.refresh()
        except Exception:
            pass                      # a bad read must not kill the widget
        self.root.after(REFRESH_S * 1000, self._tick)

    # ---------------------------------------------------------------- placing

    @staticmethod
    def work_area() -> tuple[int, int, int, int]:
        """The desktop minus the taskbar, via SPI_GETWORKAREA.

        This matters: the taskbar is always-on-top, so a window placed inside
        its band is hidden behind it however topmost we ask to be. An earlier
        version parked itself at screen_height - height - 4, which landed
        squarely under the taskbar and was simply invisible.
        """
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        try:
            ctypes.windll.user32.SystemParametersInfoW(
                0x0030, 0, ctypes.byref(rect), 0)   # SPI_GETWORKAREA
            if rect.right > rect.left and rect.bottom > rect.top:
                return rect.left, rect.top, rect.right, rect.bottom
        except (AttributeError, OSError):
            pass
        user32 = ctypes.windll.user32
        return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

    def restore_position(self) -> None:
        """The remembered spot, else bottom-right just above the taskbar."""
        self.root.update_idletasks()
        width = self.root.winfo_reqwidth()
        height = self.root.winfo_reqheight()
        left, top, right, bottom = self.work_area()

        saved = read_json(POSITION_FILE) or {}
        x, y = saved.get("x"), saved.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            x = right - width - 12
            y = bottom - height - 8        # clear of the taskbar, not under it

        # Stay inside the work area even if the resolution changed since the
        # position was saved, or the widget would vanish again.
        x = max(left, min(x, right - width))
        y = max(top, min(y, bottom - height))
        self.root.geometry("{0}x{1}+{2}+{3}".format(width, height, x, y))

    def save_position(self) -> None:
        write_atomic(POSITION_FILE, {"x": self.root.winfo_x(),
                                     "y": self.root.winfo_y()})

    # ---------------------------------------------------------------- drag

    def _press(self, event) -> None:
        self._drag = (event.x_root - self.root.winfo_x(),
                      event.y_root - self.root.winfo_y())
        self._moved = False

    def _drag_move(self, event) -> None:
        if self._drag is None:
            return
        offset_x, offset_y = self._drag
        self.root.geometry("+{0}+{1}".format(event.x_root - offset_x,
                                             event.y_root - offset_y))
        self._moved = True

    def _release(self, _event) -> None:
        """A drag repositions; a click without movement opens the panel."""
        if self._drag is None:
            return
        self._drag = None
        if self._moved:
            self.save_position()
        else:
            self.open_panel()

    def open_panel(self) -> None:
        if config.FROZEN:
            command = [sys.executable, "--panel"]
            cwd = None
        else:
            interpreter = sys.executable
            windowless = os.path.join(os.path.dirname(interpreter), "pythonw.exe")
            exe = windowless if os.path.exists(windowless) else interpreter
            command = [exe, "-m", "clausage_bar.panel"]
            cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        try:
            subprocess.Popen(command, cwd=cwd,
                             creationflags=CREATE_NO_WINDOW)
        except OSError:
            pass

    # ---------------------------------------------------------------- run

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self.save_position()
        except Exception:
            pass
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
        return Widget().run()
    except Exception:
        from .logging_setup import get, setup
        setup()
        get("widget").exception("widget failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
