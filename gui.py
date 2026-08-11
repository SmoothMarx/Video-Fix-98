#!/usr/bin/env python3
"""Video-Fix-98 — late-90s Windows 9x style GUI.

A Tkinter front-end for salvage.py with a three-pane layout:

  LEFT   Source queue — add files/folders, remove, clear; each entry is
         processed in sequence when Run is pressed.
  CENTER Pre-start / Processing / Output option categories (each toggleable),
         live realtime log (with optional auto-scroll), and the green
         ▶ Run button (bottom-right, doubled height).
  RIGHT  Output monitor — choose the output folder, watch files land in it
         in real time as processing completes.
  BOTTOM Logo + status + Run cluster; Help and About popups.

Deliberately styled like Windows 98: #C0C0C0 gray, MS Sans Serif 8pt,
raised/sunken 3D borders, no theming. Shows a 9x splash with the logo and
uses assets/icon.png for the window icon.

Requires: Python 3 + tkinter, and salvage.py + ffmpeg/ffprobe/untrunc on PATH
(or next to this file).

Usage:
    python3 gui.py
"""
import sys
import os

# ---- heartbeat: written as the first executable line to prove that the -----
# ---- Python interpreter actually started inside the frozen exe -------------
_hb = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "vf98-startup.log")
try:
    with open(_hb, "w") as _f:
        _f.write(f"Python started: {sys.version}\n")
        _f.write(f"executable: {sys.executable}\n")
        _f.write(f"argv: {sys.argv}\n")
        _f.write(f"frozen: {getattr(sys, 'frozen', False)}\n")
        _f.write(f"_MEIPASS: {getattr(sys, '_MEIPASS', 'not set')}\n")
except Exception:
    pass

import argparse
import csv
import json
import subprocess
import sys
import tempfile
import threading
import traceback

# ---- early crash guard (catches import-time failures) ---------------------
def _vf98_early_crash():
    """Write a crash log when we can't even import the rest of the stack."""
    try:
        log_path = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                                "vf98-crash.log")
        with open(log_path, "w") as f:
            f.write("Video-Fix-98 CRASH (import-time)\n")
            f.write("=" * 60 + "\n")
            traceback.print_exc(file=f)
            f.write("\n" + "=" * 60 + "\n")
    except Exception:
        pass
    sys.exit(1)

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except Exception:
    _vf98_early_crash()

VERSION = "1.3.0"

# ---- classic Win9x palette ------------------------------------------------
BG = "#C0C0C0"           # standard dialog gray
BTNFACE = "#C0C0C0"
SUNKEN_BG = "#FFFFFF"
NAVY = "#000080"         # classic title bar blue
TITLE_FG = "#FFFFFF"
RUN_GREEN = "#00A000"    # green Run button

FONT_FAMILY = "MS Sans Serif"   # Windows classic; falls back gracefully
FONT = (FONT_FAMILY, 10)
FONT_BOLD = (FONT_FAMILY, 8, "bold")

HERE = os.path.dirname(os.path.abspath(__file__))
# when frozen into an exe, bundled files live in sys._MEIPASS
_BUNDLE = getattr(sys, "_MEIPASS", HERE)
ICON_PATH = os.path.join(_BUNDLE, "assets", "icon.png")
LOGO_PATH = os.path.join(_BUNDLE, "assets", "logo.png")
_SALVAGE_NAME = "salvage.exe" if sys.platform == "win32" else "salvage"
SALVAGE_EXE = os.path.join(_BUNDLE, _SALVAGE_NAME)

SPLASH_MS = 3000   # how long the splash shows
WATCH_MS = 2000    # output-monitor refresh interval

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".ts", ".webm",
              ".m4v", ".flv", ".wmv", ".mpg", ".mpeg", ".m2ts"}

# per-variable help text for the Help popup
VARIABLE_HELP = [
    ("Fix", "Repair method. 'auto' decides from the damage; 'salvage' trims "
            "frozen frames and re-encodes; 'remux' rebuilds the container "
            "losslessly; 'untrunc' rebuilds a missing moov index."),
    ("Force full pass (sparse)", "Run the full frame pass even on sparse "
            "files. Normally skipped because no data = nothing to examine."),
    ("Force full pass (healthy)", "Run the full freezedetect pass even on "
            "apparently healthy files (no error signature, high allocation). "
            "By default, these skip the expensive scan — check this to "
            "verify them thoroughly."),
    ("CRF (quality)", "Encode quality. LOWER is better quality but a bigger "
            "file. 18 ≈ near-lossless, 23 ≈ standard, 28 ≈ small."),
    ("Preset", "Encoder speed vs size. 'ultrafast' is fastest but bigger; "
            "'slow' is slower but smaller."),
    ("Min freeze (s)", "How long (seconds) a frozen stretch must last before "
            "it is trimmed. Lower catches short freezes; higher keeps more."),
    ("Margin (s)", "Safety buffer trimmed around each frozen zone. 0 = no "
            "good frames sacrificed; higher = more conservative."),
    ("Noise (dB)", "Pixel-difference threshold for 'same frame'. Type a plain "
            "number (e.g. 60), a negative (-60), or with dB (60dB) — the "
            "program understands all forms. LOWER (more negative) is stricter."),
    ("Container", "Output container: mkv (most tolerant), mp4/mov (most "
            "compatible), avi, ts, webm, m4v, flv."),
    ("Codec", "Video codec: h264 (fast/most compatible), hevc (smaller, "
            "slower), vp9/av1 (modern, slow)."),
    ("FPS", "Output frame rate. Should match the source's real rate or "
            "playback speed will be wrong."),
    ("Resolution", "Output resolution (e.g. 1280x720). Leave empty to keep "
            "the source resolution."),
    ("Audio", "Audio handling: 'off' drops it, 'copy' keeps it if decodable, "
            "'aac' re-encodes it at 128k."),
]


class BeveledFrame(tk.Frame):
    """Raised or sunken 3D frame with the classic Win9x double border."""
    def __init__(self, parent, relief="raised", pad=4, **kw):
        super().__init__(parent, bg=BG, **kw)
        self.inner = tk.Frame(self, bg=BG)
        self.inner.pack(fill="both", expand=True, padx=pad, pady=pad)
        if relief == "sunken":
            self.config(relief="sunken", bd=2, highlightthickness=1,
                        highlightbackground="#808080")
        else:
            self.config(relief="raised", bd=2, highlightthickness=1,
                        highlightbackground="#FFFFFF")
        self.widget = self.inner


class CategoryBox(tk.Frame):
    """A sunken group box with a 9x-style etched title label on the border."""
    def __init__(self, parent, title):
        super().__init__(parent, bg=BG)
        self.outer = tk.Frame(self, bg=BG, bd=0)
        self.outer.pack(fill="both", expand=True)
        self.box = tk.Frame(self.outer, bg=BG, relief="sunken", bd=2,
                            highlightthickness=1, highlightbackground="#808080")
        self.box.pack(fill="both", expand=True, padx=2, pady=(12, 2))
        self.lbl = tk.Label(self.outer, text=title, bg=BG, fg="#000000",
                            font=FONT, padx=4)
        self.lbl.place(x=8, y=0)
        self.widget = self.box


class OptionToggle(tk.Frame):
    """A labeled checkbox + its option widget; the option is enabled only
    when the checkbox is checked (classic 9x 'enabled' behavior)."""
    def __init__(self, parent, label, widget_factory, default_checked=False):
        super().__init__(parent, bg=BG)
        self.var = tk.BooleanVar(value=default_checked)
        self.cb = tk.Checkbutton(
            self, text=label, variable=self.var, bg=BG, activebackground=BG,
            font=FONT, command=self._sync)
        self.cb.pack(side="left", anchor="w")
        self.control = widget_factory(self)
        self.control.pack(side="left", padx=(4, 0))
        self._sync()

    def _sync(self):
        state = "normal" if self.var.get() else "disabled"
        try:
            self.control.config(state=state)
        except tk.TclError:
            pass

    def value(self):
        return self.var.get()


class ComboFactory:
    def __init__(self, values, getter, setter):
        self.values = values
        self.getter = getter
        self.setter = setter

    def __call__(self, parent):
        var = tk.StringVar(value=self.getter())
        cmb = ttk.Combobox(parent, textvariable=var, values=self.values,
                           state="readonly", width=9, font=FONT)
        cmb.bind("<<ComboboxSelected>>", lambda e: self.setter(var.get()))
        return cmb


class SpinFactory:
    def __init__(self, getter, setter, from_, to, inc=1, width=6):
        self.getter = getter
        self.setter = setter
        self.from_, self.to, self.inc = from_, to, inc
        self.width = width

    def __call__(self, parent):
        var = tk.StringVar(value=str(self.getter()))
        sp = tk.Spinbox(parent, from_=self.from_, to=self.to,
                        increment=self.inc, textvariable=var, width=self.width,
                        font=FONT, bg=SUNKEN_BG, relief="sunken", bd=2)
        sp.bind("<FocusOut>", lambda e: self._commit(var))
        sp.bind("<Return>", lambda e: self._commit(var))
        return sp

    def _commit(self, var):
        try:
            self.setter(type(self.getter())(var.get()))
        except (ValueError, TypeError):
            var.set(str(self.getter()))



class EntryFactory:
    def __init__(self, getter, setter, width=24):
        self.getter = getter
        self.setter = setter
        self.width = width

    def __call__(self, parent):
        var = tk.StringVar(value=str(self.getter()))
        en = tk.Entry(parent, textvariable=var, width=self.width, bg=SUNKEN_BG,
                      relief="sunken", bd=2, font=FONT)
        en.bind("<FocusOut>", lambda e: self.setter(var.get()))
        return en


def splash_icon(root):
    """Returns a PhotoImage sized for the splash screen, or None."""
    if not os.path.exists(LOGO_PATH):
        return None
    try:
        img = tk.PhotoImage(file=LOGO_PATH)
        scale = 300 / img.width()
        if scale < 1:
            img = img.subsample(int(1 / scale) or 1)
        return img
    except Exception:
        return None


def small_logo(root, width=96):
    """Returns a downscaled PhotoImage of the logo, or None."""
    if not os.path.exists(LOGO_PATH):
        return None
    try:
        img = tk.PhotoImage(file=LOGO_PATH)
        scale = width / img.width()
        if scale < 1:
            img = img.subsample(int(1 / scale) or 1)
        return img
    except Exception:
        return None


def show_splash(root, main_cb):
    """Classic 9x splash: gray raised frame, navy title, logo, version."""
    splash = tk.Toplevel(root)
    splash.overrideredirect(True)
    splash.configure(bg=BG)

    outer = tk.Frame(splash, bg=BG, relief="raised", bd=3)
    outer.pack(padx=6, pady=6)

    banner = tk.Label(outer, text="Video-Fix-98", bg=NAVY, fg=TITLE_FG,
                      font=(FONT_FAMILY, 14, "bold"), padx=24, pady=6)
    banner.pack(fill="x")

    logo = splash_icon(root)
    if logo:
        img_lbl = tk.Label(outer, image=logo, bg="#FFFFFF", relief="sunken", bd=2)
        img_lbl.image = logo
        img_lbl.pack(padx=12, pady=8)
    else:
        tk.Label(outer, text="Corrupt video checker & repair", bg=BG,
                 font=FONT, pady=16).pack(padx=12, pady=8)

    sub = tk.Label(outer, text="Reads the datastream, keeps every good frame,\n"
                               "drops the lies.", bg=BG, font=FONT)
    sub.pack(pady=(0, 6))

    prog = tk.Frame(outer, bg=BG, relief="sunken", bd=2, height=10, width=180)
    prog.pack(pady=6)
    prog.pack_propagate(False)
    fill = tk.Frame(prog, bg=NAVY, width=0)
    fill.pack(side="left", fill="y")

    def animate(step=0):
        if step >= 100:
            splash.destroy()
            main_cb()
            return
        try:
            fill.config(width=int(180 * step / 100))
        except tk.TclError:
            return
        splash.after(SPLASH_MS // 100, animate, step + 4)

    splash.update_idletasks()
    w = splash.winfo_reqwidth()
    h = splash.winfo_reqheight()
    x = (splash.winfo_screenwidth() - w) // 2
    y = (splash.winfo_screenheight() - h) // 2
    splash.geometry(f"+{x}+{y}")
    animate()


def center_window(win, parent=None):
    """Center a popup within the parent window (or screen if no parent).
    The window stays moveable (normal title bar)."""
    win.update_idletasks()
    w = win.winfo_reqwidth()
    h = win.winfo_reqheight()
    if parent is not None and parent.winfo_viewable():
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        x = px + max(0, (pw - w) // 2)
        y = py + max(0, (ph - h) // 2)
    else:
        x = (win.winfo_screenwidth() - w) // 2
        y = (win.winfo_screenheight() - h) // 2
    win.geometry(f"+{x}+{y}")


def human_bytes_short(n):
    """Bytes → compact human string: 1.2MB, 340KB, etc."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "-"
    if n == 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n}{unit}" if unit == "B" else f"{n/1024:.1f}{unit}"
        n //= 1024
    return f"{n:.1f}TB"


class SalvageGUI:
    def __init__(self, root):
        self.root = root
        root.title("Video-Fix-98")
        root.configure(bg=BG)
        root.resizable(True, True)
        self._set_icon(root)
        root.geometry("900x680")

        self.opts = {
            "fix": "auto",
            "force_pass": False,
            "force_pass_healthy": False,
            "crf": 20,
            "preset": "veryfast",
            "min_freeze": 2.0,
            "margin": 1.0,
            "noise": "-60",
            "container": "mkv",
            "codec": "h264",
            "fps": 50,
            "resolution": "",
            "audio": "copy",
        }
        self.source_queue = []
        out = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                           "Video Fixer Output")
        try:
            os.makedirs(out, exist_ok=True)
            self.out_dir = out
        except (OSError, PermissionError):
            self.out_dir = ""
        self.running = False
        self._stopping = False
        self._resizing = False
        self._check_results = {}
        self._checked_files = set()
        self._report_dir = tempfile.mkdtemp(prefix="vf98_reports_")
        self.autoscroll = tk.BooleanVar(value=True)
        self.toggles = {}
        self._build()
        self._load_session()
        self.est_label.config(text="")
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        # debounced resize — skip layout recalc during drag
        self._resize_job = None
        def _on_resize(event):
            if not self._resizing:
                self._resizing = True
            if self._resize_job is not None:
                root.after_cancel(self._resize_job)
            def _settled():
                self._resizing = False
                root.update_idletasks()
            self._resize_job = root.after(50, _settled)
        root.bind("<Configure>", _on_resize, add="+")

    def _on_close(self):
        """Clean up temp files on exit."""
        try:
            import shutil
            shutil.rmtree(self._report_dir, ignore_errors=True)
        except Exception:
            pass
        self.root.destroy()

    def _set_icon(self, win):
        if not os.path.exists(ICON_PATH):
            return
        try:
            img = tk.PhotoImage(file=ICON_PATH)
            win.iconphoto(True, img)
            win._icon_ref = img
        except Exception:
            pass

    # ---------------------------------------------------------------- UI
    def _build(self):
        outer = BeveledFrame(self.root, relief="raised")
        outer.pack(fill="both", expand=True, padx=2, pady=2)
        body = outer.widget

        title = tk.Label(body, text="Video-Fix-98  -  Corrupt Video Checker & Repair",
                         bg=NAVY, fg=TITLE_FG, font=FONT_BOLD,
                         anchor="w", padx=6, pady=3)
        title.pack(fill="x", pady=(0, 6))

        panes = tk.PanedWindow(body, orient=tk.HORIZONTAL, bg=BG, sashwidth=6)
        panes.pack(fill="both", expand=True)

        source_frame = tk.Frame(panes, bg=BG)
        panes.add(source_frame, width=200, minsize=100)
        self._build_source_pane(source_frame)

        center_frame = tk.Frame(panes, bg=BG)
        panes.add(center_frame, stretch="always")
        self._build_center_pane(center_frame)

        watch_frame = tk.Frame(panes, bg=BG)
        panes.add(watch_frame, width=230, minsize=120)
        self._build_watch_pane(watch_frame)


    def _build_bottom_bar(self, parent):
        """Logo centered + Run/Stop right-aligned."""
        bottom = tk.Frame(parent, bg=BG)
        bottom.pack(fill="x", padx=2, pady=(10, 8))
        self._bottom = bottom  # for progress-bar insertion later

        # logo: placed at true 50%, independent of right cluster width
        logo = small_logo(self.root, width=96)
        if logo:
            lbl = tk.Label(bottom, image=logo, bg=BG)
            lbl.image = logo
        else:
            lbl = tk.Label(bottom, text="Video-Fix-98", bg=BG, font=FONT_BOLD)
        lbl.place(relx=0.5, rely=0.5, anchor="center")
        lbl.bind("<Button-1>", lambda e: self._about())

        # right cluster: Run → Stop
        right = tk.Frame(bottom, bg=BG)
        right.pack(side="right")
        self.run_btn = tk.Button(right, text="\u25B6  Run", command=self._run,
                                 bg=RUN_GREEN, fg="#FFFFFF",
                                 font=(FONT_FAMILY, 12, "bold"),
                                 relief="raised", bd=3, padx=28, pady=10,
                                 activebackground="#00B000")
        self.run_btn.pack(side="top")
        self.stop_btn = tk.Button(right, text="\u25A0  Stop", command=self._stop,
                                  bg="#CC0000", fg="#FFFFFF",
                                  font=(FONT_FAMILY, 12, "bold"),
                                  relief="raised", bd=3, padx=28, pady=6,
                                  activebackground="#DD0000")
        # hidden until a run starts
        self.stop_btn.pack(side="top", pady=(4, 0))
        self.stop_btn.pack_forget()

    def _build_source_pane(self, parent):
        box = CategoryBox(parent, " Source ")
        box.pack(side="left", fill="both", expand=True)
        w = box.widget

        row = tk.Frame(w, bg=BG)
        row.pack(fill="x", padx=4, pady=4)
        tk.Button(row, text="Add Files...", command=self._add_files, bg=BTNFACE,
                  font=(FONT_FAMILY, 10, "bold"), relief="raised", bd=2, padx=8).pack(side="left")
        tk.Button(row, text="Add Folder...", command=self._add_folder, bg=BTNFACE,
                  font=(FONT_FAMILY, 10, "bold"), relief="raised", bd=2, padx=8).pack(side="left", padx=(6, 0))
        self.include_subfolders = tk.BooleanVar(value=False)
        tk.Checkbutton(row, text="Sub-folders", variable=self.include_subfolders,
                       bg=BG, activebackground=BG, font=FONT).pack(side="left", padx=(6, 0))

        self.queue_list = tk.Listbox(w, bg=SUNKEN_BG, relief="sunken", bd=2,
                                      font=("Courier", 10), selectmode="extended")
        self.queue_list.pack(fill="both", expand=True, padx=4, pady=2)

        row2 = tk.Frame(w, bg=BG)
        row2.pack(fill="x", padx=4, pady=4)
        tk.Button(row2, text="Remove", command=self._remove_selected, bg=BTNFACE,
                  font=(FONT_FAMILY, 10, "bold"), relief="raised", bd=2, padx=10).pack(side="left")
        tk.Button(row2, text="Clear", command=self._clear_queue, bg=BTNFACE,
                  font=(FONT_FAMILY, 10, "bold"), relief="raised", bd=2, padx=10).pack(side="left", padx=(6, 0))
        self.check_btn = tk.Button(row2, text="Check", command=self._check_all,
                                   bg="#00A000", fg="#FFFFFF",
                                   font=(FONT_FAMILY, 10, "bold"), relief="raised", bd=2, padx=10)
        self.check_btn.pack(side="right", padx=(4, 0))

    def _build_center_pane(self, parent):
        center = tk.Frame(parent, bg=BG)
        center.pack(side="left", fill="both", expand=True)

        cats = tk.Frame(center, bg=BG)
        cats.pack(fill="x", padx=2, pady=(0, 2))

        # Pre-start
        pre = CategoryBox(cats, " Pre-start ")
        pre.pack(side="left", fill="both", expand=True, padx=(0, 3))
        self._add_toggle(pre.widget, "Fix", ComboFactory(
            ("auto", "salvage", "remux", "untrunc", "none"),
            lambda: self.opts["fix"], lambda v: self.opts.__setitem__("fix", v)))
        self._add_toggle(pre.widget, "Force full pass (sparse)", None,
                         checked=False)
        self._add_toggle(pre.widget, "Force full pass (healthy)", None,
                         checked=False)
        self.import_btn = tk.Button(
            pre.widget, text="Import", command=self._import_session,
            bg=BTNFACE, font=(FONT_FAMILY, 10, "bold"), relief="raised", bd=2, padx=10, pady=2)
        self.import_btn.pack(fill="x", padx=6, pady=3)

        # Processing (incl. preset per user request)
        pro = CategoryBox(cats, " Processing ")
        pro.pack(side="left", fill="both", expand=True, padx=3)
        self._add_toggle(pro.widget, "CRF (quality)", SpinFactory(
            lambda: self.opts["crf"], lambda v: self.opts.__setitem__("crf", int(v)),
            0, 51))
        self._add_toggle(pro.widget, "Preset", ComboFactory(
            ("ultrafast", "veryfast", "fast", "medium", "slow", "veryslow"),
            lambda: self.opts["preset"], lambda v: self.opts.__setitem__("preset", v)))
        self._add_toggle(pro.widget, "Min freeze (s)", SpinFactory(
            lambda: self.opts["min_freeze"], lambda v: self.opts.__setitem__("min_freeze", float(v)),
            0.1, 60.0, 0.1))
        self._add_toggle(pro.widget, "Margin (s)", SpinFactory(
            lambda: self.opts["margin"], lambda v: self.opts.__setitem__("margin", float(v)),
            0.0, 30.0, 0.1))
        self._add_toggle(pro.widget, "Noise (dB)", SpinFactory(
            lambda: int(self.opts["noise"]),
            lambda v: self.opts.__setitem__("noise", str(v)),
            -90, -10, 10, width=6))

        # Output
        out = CategoryBox(cats, " Output ")
        out.pack(side="left", fill="both", expand=True, padx=(3, 0))
        self._add_toggle(out.widget, "Container", ComboFactory(
            ("mkv", "mp4", "mov", "avi", "ts", "webm", "m4v", "flv"),
            lambda: self.opts["container"], lambda v: self.opts.__setitem__("container", v)))
        self._add_toggle(out.widget, "Codec", ComboFactory(
            ("h264", "hevc", "vp9", "av1"),
            lambda: self.opts["codec"], lambda v: self.opts.__setitem__("codec", v)))
        self._add_toggle(out.widget, "FPS", SpinFactory(
            lambda: self.opts["fps"], lambda v: self.opts.__setitem__("fps", int(v)),
            1, 240))
        self._add_toggle(out.widget, "Resolution", EntryFactory(
            lambda: self.opts["resolution"], lambda v: self.opts.__setitem__("resolution", v),
            width=9), checked=False)
        self._add_toggle(out.widget, "Audio", ComboFactory(
            ("off", "copy", "aac"),
            lambda: self.opts["audio"], lambda v: self.opts.__setitem__("audio", v)))

        # ---- notebook: Report | Progress Log (same position) ----
        self._notebook = ttk.Notebook(center)
        self._notebook.pack(fill="both", expand=True, padx=4, pady=(2, 2))

        # Log tab
        log_frame = BeveledFrame(self._notebook, relief="sunken")
        lf = log_frame.widget

        log_header = tk.Frame(lf, bg=BG)
        log_header.pack(fill="x", padx=2, pady=(0, 1))
        tk.Label(log_header, text="Progress log (realtime)", bg=BG,
                 font=FONT_BOLD).pack(side="left")
        tk.Checkbutton(log_header, text="Auto-scroll", variable=self.autoscroll,
                       bg=BG, activebackground=BG, font=FONT).pack(side="right")

        self.log = tk.Text(lf, height=10, width=70, bg=SUNKEN_BG,
                           relief="sunken", bd=2, font=("Courier", 8),
                           wrap="word", state="disabled")
        sb = tk.Scrollbar(lf, command=self.log.yview)
        self.log.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True, padx=(2, 0))
        self._notebook.add(log_frame, text="Progress Log")

        # Report tab (always visible)
        self._build_report_section(self._notebook)
        self._notebook.add(self._report_frame, text="Report")

        # Results tab (populated after repair)
        self._results_frame = tk.Frame(self._notebook, bg=BG)
        self._notebook.add(self._results_frame, text="Results")

        # progress bar — native green, below the log, in center column
        style = ttk.Style()
        style.configure("Green.Horizontal.TProgressbar",
                        troughcolor=BG, background="#00A000")
        self.progress = ttk.Progressbar(center, mode="determinate", maximum=100,
                                        style="Green.Horizontal.TProgressbar")

        # status bar below progress bar: status centered, estimate right
        self._status_frame = tk.Frame(center, bg=BG)
        self._status_frame.pack(fill="x", padx=4, pady=(0, 2))
        self.est_label = tk.Label(self._status_frame, text="", bg=BG, font=FONT,
                                  fg="#666666")
        self.est_label.pack(side="right")
        self.status = tk.Label(self._status_frame, text="Ready", bg=BG, font=FONT)
        self.status.pack(side="left", fill="x", expand=True)

        # bottom bar lives INSIDE the center column (sidebars span full height)
        self._build_bottom_bar(center)

    def _build_watch_pane(self, parent):
        box = CategoryBox(parent, " Output folder ")
        box.pack(side="left", fill="both", expand=True)
        w = box.widget

        row = tk.Frame(w, bg=BG)
        row.pack(fill="x", padx=4, pady=4)
        tk.Button(row, text="Browse...", command=self._browse_out, bg=BTNFACE,
                  font=(FONT_FAMILY, 10, "bold"), relief="raised", bd=2, padx=8).pack(side="left")
        default_name = os.path.basename(self.out_dir) if self.out_dir else "(not set)"
        self.watch_dir_lbl = tk.Label(row, text=default_name, bg=BG, font=FONT,
                                      anchor="w")
        self.watch_dir_lbl.pack(side="left", padx=(4, 0), fill="x", expand=True)

        self.output_same_as_source = tk.BooleanVar(value=False)
        tk.Checkbutton(w, text="Same as source folder", variable=self.output_same_as_source,
                       bg=BG, activebackground=BG, font=FONT).pack(anchor="w", padx=6, pady=(0, 2))

        self.watch_list = tk.Listbox(w, bg=SUNKEN_BG, relief="sunken", bd=2,
                                     font=("Courier", 10), selectmode="extended")
        self.watch_list.pack(fill="both", expand=True, padx=4, pady=2)
        self.watch_count = tk.Label(w, text="0 files", bg=BG, font=FONT, anchor="w")
        self.watch_count.pack(fill="x", padx=6, pady=(0, 4))

        # Help | Exit — equidistant across sidebar width
        help_row = tk.Frame(w, bg=BG)
        help_row.pack(fill="x", padx=4, pady=(4, 4))
        help_row.grid_columnconfigure(0, weight=1, uniform="h")
        help_row.grid_columnconfigure(1, weight=1, uniform="h")
        for i, (label, cb) in enumerate((("Help", self._help),
                                          ("Exit \U0001F6AA", self.root.destroy))):
            tk.Button(help_row, text=label, bg=BTNFACE,
                      font=(FONT_FAMILY, 10, "bold"), relief="raised",
                      bd=2, padx=8, command=cb).grid(row=0, column=i, sticky="ew", padx=1)

        self._watch_tick()

    def _build_report_section(self, parent):
        """Inline check-results table between options and log."""
        self._report_frame = tk.Frame(parent, bg=BG)
        self._report_tree = None

    def _refresh_report_table(self):
        """Populate the Report tab from _check_results."""
        if self._report_tree:
            self._report_tree.destroy()
        # clear all old widgets from the report frame
        for child in list(self._report_frame.winfo_children()):
            child.destroy()

        if not self._check_results:
            self._notebook.select(0)
            return

        self._notebook.select(1)  # switch to report tab

        cols = [
            ("checked", "✓", 35), ("file", "File", 120), ("error", "Error", 90),
            ("decodable_pct", "% recovered", 55), ("good_seconds", "Salvaged", 70),
        ]
        tree = ttk.Treeview(self._report_frame,
                            columns=[c[0] for c in cols],
                            show="headings", height=min(6, len(self._check_results)))
        ttk.Style().configure("Treeview", rowheight=28)
        for key, label, width in cols:
            tree.heading(key, text=label)
            tree.column(key, width=width, anchor="center" if key != "file" else "w")

        sb = ttk.Scrollbar(self._report_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)

        # header with Repair button
        hdr = tk.Frame(self._report_frame, bg=BG)
        hdr.pack(fill="x", padx=2, pady=(0, 2))
        tk.Label(hdr, text=f"Check Results — {len(self._check_results)} file(s)",
                 bg=BG, font=FONT_BOLD).pack(side="left")
        # filter dropdown
        self._report_filter = tk.StringVar(value="All")
        flt = ttk.Combobox(hdr, textvariable=self._report_filter,
                           values=["All", "Corrupt only", "Healthy only"],
                           state="readonly", width=14, font=FONT)
        flt.pack(side="left", padx=(8, 0))
        flt.bind("<<ComboboxSelected>>", lambda e: self._refresh_report_table())
        tk.Button(hdr, text="Repair Checked", command=self._run,
                  bg=RUN_GREEN, fg="#FFFFFF", font=FONT,
                  relief="raised", bd=2, padx=6, pady=1).pack(side="right")
        tk.Button(hdr, text="Save As...", command=self._save_session_as,
                  bg=BTNFACE, font=FONT, relief="raised",
                  bd=2, padx=6, pady=1).pack(side="right", padx=(4, 0))

        tree.pack(side="left", fill="both", expand=True, padx=2, pady=2)
        sb.pack(side="right", fill="y", pady=2)

        self._check_rows_iid = {}
        filter_mode = self._report_filter.get() if hasattr(self, "_report_filter") else "All"
        for path, info in self._check_results.items():
            err = (info.get("error") or "").lower()
            # apply filter
            if filter_mode == "Corrupt only" and ("none detected" in err or "clean (quick)" in err):
                continue
            if filter_mode == "Healthy only" and "none detected" not in err and "clean (quick)" not in err:
                continue
            checked = "☑" if path in self._checked_files else "☐"
            gs = info.get("good_seconds", 0)
            if isinstance(gs, (int, float)):
                m, s = divmod(int(gs), 60)
                h, m = divmod(m, 60)
                gs_str = f"{h:02d}:{m:02d}:{s:02d}"
            else:
                gs_str = str(gs)
            vals = [checked, os.path.basename(path), info.get("error", "?"),
                    f"{info.get('decodable_pct', '?')}%", gs_str]
            iid = tree.insert("", "end", values=vals)
            self._check_rows_iid[iid] = path

        def on_click(event):
            iid = tree.identify_row(event.y)
            if not iid: return
            path = self._check_rows_iid.get(iid)
            if not path: return
            if path in self._checked_files:
                self._checked_files.discard(path)
            else:
                self._checked_files.add(path)
            tree.set(iid, "checked", "☑" if path in self._checked_files else "☐")
            self._update_estimate()
        tree.bind("<Button-1>", on_click)

        all_ck = [len(self._checked_files) == len(self._check_results)]
        def on_hdr(event):
            if tree.identify_region(event.x, event.y) != "heading": return
            if tree.identify_column(event.x) != "#1": return
            all_ck[0] = not all_ck[0]
            self._checked_files = set(self._check_results.keys()) if all_ck[0] else set()
            for iid, path in self._check_rows_iid.items():
                tree.set(iid, "checked", "☑" if path in self._checked_files else "☐")
            self._update_estimate()
        tree.bind("<ButtonRelease-1>", on_hdr, add="+")
        self._report_tree = tree

    def _add_toggle(self, parent, label, factory, checked=False):
        if factory is None:
            t = OptionToggle(parent, label, lambda p: tk.Frame(p, bg=BG),
                             default_checked=checked)
            t.pack(fill="x", padx=6, pady=3, anchor="w")
            self.toggles[label] = t
            return t
        t = OptionToggle(parent, label, factory, default_checked=checked)
        t.pack(fill="x", padx=6, pady=3, anchor="w")
        self.toggles[label] = t
        return t

    # ----------------------------------------------------------- queue
    def _add_files(self):
        paths = filedialog.askopenfilenames(title="Add source files")
        for p in paths:
            self._queue_add(p)

    def _add_folder(self):
        p = filedialog.askdirectory(title="Add source folder")
        if p:
            try:
                if self.include_subfolders.get():
                    for root, _dirs, files in os.walk(p):
                        for name in sorted(files):
                            if os.path.splitext(name)[1].lower() in VIDEO_EXTS:
                                self._queue_add(os.path.join(root, name))
                else:
                    for name in sorted(os.listdir(p)):
                        if os.path.splitext(name)[1].lower() in VIDEO_EXTS:
                            self._queue_add(os.path.join(p, name))
            except OSError:
                messagebox.showerror("Video-Fix-98",
                    f"Could not read folder:\n{p}")

    def _queue_add(self, path):
        if path not in self.source_queue:
            self.source_queue.append(path)
            self.queue_list.insert("end", os.path.basename(path))

    def _remove_selected(self):
        sel = list(self.queue_list.curselection())
        for idx in reversed(sel):
            self.queue_list.delete(idx)
            del self.source_queue[idx]
        self._invalidate_check()

    def _clear_queue(self):
        self.queue_list.delete(0, "end")
        self.source_queue = []
        self._invalidate_check()

    def _invalidate_check(self):
        """Clear cached check results when queue changes."""
        self._check_results = {}
        self._checked_files = set()
        self.est_label.config(text="")
        self._refresh_report_table()
        try:
            os.remove(self._session_path())
        except OSError:
            pass

    # ----------------------------------------------------------- output
    def _browse_out(self):
        p = filedialog.askdirectory(title="Choose output folder to monitor")
        if p:
            self.out_dir = p
            short = os.path.basename(p) or p
            self.watch_dir_lbl.config(text=short)
            self._watch_refresh()

    def _watch_tick(self):
        if not self._resizing and self.out_dir:
            self._watch_refresh()
        self.root.after(WATCH_MS, self._watch_tick)

    def _watch_refresh(self):
        if not self.out_dir or not os.path.isdir(self.out_dir):
            self.watch_count.config(text="(not set)")
            return
        try:
            entries = os.listdir(self.out_dir)
        except OSError:
            self.watch_count.config(text="(unreadable)")
            return
        vids = sorted(e for e in entries
                      if os.path.splitext(e)[1].lower() in VIDEO_EXTS)
        self.watch_list.delete(0, "end")
        for e in vids:
            self.watch_list.insert("end", e)
        self.watch_count.config(text=f"{len(vids)} video file(s)")

    # ------------------------------------------------------------ run
    def _log(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text)
        if self.autoscroll.get():
            self.log.see("end")
        self.log.config(state="disabled")

    def _build_cmd(self, src, report_path=None):
        cmd = [self._runner()] if getattr(sys, "frozen", False) else [sys.executable, self._tool_path()]
        cmd += ["--gui"]
        cmd += [src]
        cmd += ["--mode", "repair"]
        cmd += ["--fix", self.opts["fix"]]
        cmd += ["--container", self.opts["container"]]
        cmd += ["--codec", self.opts["codec"]]
        cmd += ["--audio-mode", self.opts["audio"]]
        if self.opts.get("force_pass"):
            cmd += ["--force-pass"]
        if self.opts.get("force_pass_healthy"):
            cmd += ["--no-quick"]
        cmd += ["--min-freeze", str(self.opts["min_freeze"])]
        # equals-form so a leading '-' (e.g. -60dB) isn't parsed as a flag
        cmd += [f"--noise={self.opts['noise']}"]
        cmd += ["--margin", str(self.opts["margin"])]
        cmd += ["--crf", str(self.opts["crf"])]
        cmd += ["--preset", self.opts["preset"]]
        cmd += ["--fps", str(self.opts["fps"])]
        if self.opts["resolution"]:
            cmd += ["--resolution", self.opts["resolution"]]
        if self.out_dir:
            out = os.path.dirname(src) if self.output_same_as_source.get() else self.out_dir
            cmd += ["--out-dir", out]
        if report_path:
            cmd += ["--report", report_path]
        return cmd

    def _runner(self):
        """When frozen, run the bundled salvage executable directly."""
        if getattr(sys, "frozen", False):
            return SALVAGE_EXE if os.path.exists(SALVAGE_EXE) else "salvage"
        return sys.executable

    def _tool_path(self):
        cand = os.path.join(HERE, "salvage.py")
        return cand if os.path.exists(cand) else "salvage.py"

    def _run(self):
        if not self.source_queue:
            messagebox.showerror("Video-Fix-98", "Add source files/folders to the queue first.")
            return
        if self.running:
            return
        # Filter to only checked files when check results exist
        if self._check_results:
            queue = [p for p in self.source_queue
                     if p in self._checked_files]
            if not queue:
                messagebox.showinfo("Video-Fix-98",
                    "No files selected for repair.\nCheck files first, then tick the ones to repair.")
                return
        else:
            queue = list(self.source_queue)
        if not self._confirm_large_batch("run"):
            return
        self.running = True
        self.run_btn.config(state="disabled")
        self.stop_btn.pack(side="top", pady=(4, 0))
        self._set_status("Starting...")
        self._report_dir = tempfile.mkdtemp(prefix="vf98_reports_")
        self._log(f"\n--- starting {len(queue)} item(s) ---\n")
        t = threading.Thread(target=self._worker, args=(queue,), daemon=True)
        t.start()

    def _stop(self):
        """Terminate the running salvage process."""
        self._stopping = True
        if hasattr(self, "_proc") and self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._log("\n--- STOPPED by user ---\n")

    # ------------------------------------------------------- check workflow
    def _confirm_large_batch(self, mode):
        """Warn when queueing 20+ files. Returns True to continue."""
        n = len(self.source_queue)
        if n < 20:
            return True
        per_file = "~5s each (quick)" if mode == "check" else "1-5 min each"
        est = f"~{n * 5 // 60} min" if mode == "check" else f"~{n // 2}-{n * 5} min"
        return messagebox.askokcancel(
            "Video-Fix-98 — Large Batch",
            f"You have {n} files queued.\n\n"
            f"Estimated: {est} ({per_file})\n\n"
            f"Continue?",
            icon="warning")

    def _check_all(self):
        """Run salvage --mode check on every file in the queue."""
        if not self.source_queue:
            messagebox.showerror("Video-Fix-98", "Add source files to the queue first.")
            return
        if self.running:
            return
        if not self._confirm_large_batch("check"):
            return
        self.running = True
        self._stopping = False
        self.check_btn.config(state="disabled")
        self.run_btn.config(state="disabled")
        self._check_results = {}
        self._checked_files = set()
        self._set_status("Checking...")
        self._log("\n--- checking " + str(len(self.source_queue)) + " file(s) ---\n")
        self.root.after(0, lambda: self.progress.pack(
            fill="x", padx=4, pady=(4, 4), before=self._bottom))
        self.root.after(0, lambda: self.progress.configure(value=0))
        t = threading.Thread(target=self._check_worker, daemon=True)
        t.start()

    def _check_worker(self):
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        total = len(self.source_queue)
        for i, src in enumerate(self.source_queue, 1):
            if self._stopping:
                break
            name = os.path.basename(src)
            self.root.after(0, self._set_status, f"Checking [{i}/{total}] {name}")
            self.root.after(0, self._log, f"\n[{i}/{total}] checking {name}...\n")
            try:
                proc = subprocess.Popen(
                    [self._runner(), "--gui", "--mode", "check", "--report",
                     os.path.join(self._report_dir, f"check_{i}.csv"), src]
                    if getattr(sys, "frozen", False)
                    else [sys.executable, self._tool_path(), "--gui", "--mode", "check",
                      "--report", os.path.join(self._report_dir, f"check_{i}.csv"), src],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, creationflags=creationflags)
                self._proc = proc
                for line in proc.stdout:
                    if line.startswith("VF98PCT:"):
                        try:
                            pct = int(line.split(":")[1])
                            self.root.after(0, lambda v=pct: self.progress.configure(value=v))
                            self.root.after(0, lambda p=pct, n=name, j=i, t=total:
                                self._set_status(
                                    f"Checking [{j}/{t}] {n} — {p}%"))
                        except (ValueError, IndexError):
                            pass
                    else:
                        self.root.after(0, self._log, line)
                proc.wait()
                # parse the CSV
                csv_path = os.path.join(self._report_dir, f"check_{i}.csv")
                if os.path.exists(csv_path):
                    with open(csv_path, newline="", encoding="utf-8") as f:
                        rows = list(csv.DictReader(f))
                        if rows:
                            self._check_results[src] = rows[0]
                            # auto-check only corrupt files; healthy = unchecked
                            err = rows[0].get("error", "").lower()
                            if "none detected" not in err and "clean (quick)" not in err.lower():
                                self._checked_files.add(src)
                            self.root.after(0, self._refresh_report_table)
                            self.root.after(0, self._update_estimate)
                self.root.after(0, self._log,
                    f"[{i}/{total}] check done (exit {proc.returncode})\n")
            except Exception as e:
                self.root.after(0, self._log, f"[{i}/{total}] error: {e}\n")
        self.root.after(0, self._check_done)

    def _check_done(self):
        self.running = False
        self.check_btn.config(state="normal")
        self.run_btn.config(state="normal")
        self.root.after(0, self.progress.pack_forget)
        n = len(self._check_results)
        if n > 0:
            self._set_status(f"Check complete: {n} file(s)")
            self._save_session()
            self._update_estimate()
            self._refresh_report_table()
        else:
            self._set_status("Check: no results")
            self._log("no results — check reports could not be read\n")

    # ------------------------------------------------------- session persistence
    def _session_path(self):
        return os.path.join(tempfile.gettempdir(), "vf98_last_check.json")

    def _save_session(self):
        try:
            data = {
                "files": list(self._check_results.keys()),
                "results": self._check_results,
                "checked": list(self._checked_files),
            }
            with open(self._session_path(), "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _save_session_as(self):
        """Export check results to a user-chosen JSON file."""
        path = filedialog.asksaveasfilename(
            title="Save check results",
            defaultextension=".json",
            initialfile="vf98_check_results.json",
            filetypes=[("JSON files", "*.json")])
        if not path:
            return
        try:
            data = {
                "files": list(self._check_results.keys()),
                "results": self._check_results,
                "checked": list(self._checked_files),
            }
            with open(path, "w") as f:
                json.dump(data, f)
            self._log(f"\ncheck results saved: {path}\n")
        except Exception as e:
            messagebox.showerror("Video-Fix-98", f"Could not save:\n{e}")

    def _import_session(self):
        """Load a previously saved session JSON chosen by the user."""
        path = filedialog.askopenfilename(
            title="Import check session",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
            self._check_results = data.get("results", {})
            self._checked_files = set(data.get("checked", self._check_results.keys()))
            if self._check_results:
                self._update_estimate()
                self._set_status(f"Imported: {len(self._check_results)} file(s)")
                self._refresh_report_table()
        except Exception as e:
            messagebox.showerror("Video-Fix-98", f"Could not import session:\n{e}")

    def _load_session(self):
        try:
            sp = self._session_path()
            if not os.path.exists(sp):
                return
            with open(sp) as f:
                data = json.load(f)
            # results dict needs string keys for JSON round-trip
            self._check_results = data.get("results", {})
            self._checked_files = set(data.get("checked", []))
            # verify files still exist
            self._checked_files = {p for p in self._checked_files if p in self._check_results}
            if self._check_results:
                self._update_estimate()
                self._set_status(f"Session loaded: {len(self._check_results)} file(s)")
        except Exception:
            pass

    def _set_status(self, text):
        self.status.config(text=text)

    def _worker(self, queue):
        total = len(queue)
        ok = 0
        report_files = []
        # Show progress bar (between log and bottom bar)
        self.root.after(0, lambda: self.progress.pack(
            fill="x", padx=4, pady=(4, 4), before=self._bottom))
        self.root.after(0, lambda: self.progress.configure(value=0))
        self._last_progress_update = 0  # throttle: update bar every 3s
        import time as _time
        # CREATE_NO_WINDOW on Windows — suppresses salvage.exe console popup
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        for i, src in enumerate(queue, 1):
            if self._stopping:
                break
            name = os.path.basename(src)
            self.root.after(0, self._set_status, f"Running [{i}/{total}] {name}")
            report_path = os.path.join(self._report_dir, f"report_{i}.csv")
            report_files.append(report_path)
            cmd = self._build_cmd(src, report_path)
            self.root.after(0, self._log, f"\n[{i}/{total}] $ "
                            + " ".join(cmd) + "\n")
            try:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, creationflags=creationflags)
                for line in self._proc.stdout:
                    if line.startswith("VF98PHASE:"):
                        phase = line.split(":", 1)[1].strip()
                        self.root.after(0, lambda p=phase, n=name, j=i, t=total:
                            self._set_status(f"Running [{j}/{t}] {n} — {p}"))
                    elif line.startswith("VF98PCT:"):
                        try:
                            pct = int(line.split(":")[1])
                            now = _time.time()
                            if now - self._last_progress_update >= 3 or pct >= 100:
                                self.root.after(0, lambda v=pct: self.progress.configure(value=v))
                                self.root.after(0, lambda p=pct, n=name: self._set_status(
                                    f"Running [{i}/{total}] {n} — {p}%"))
                                self._last_progress_update = now
                        except (ValueError, IndexError):
                            pass
                    else:
                        self.root.after(0, self._log, line)
                self._proc.wait()
                if self._proc.returncode == 0:
                    ok += 1
                self.root.after(0, self._log, f"[{i}/{total}] exit {self._proc.returncode}\n")
                # merge repair results back into check results
                if src in self._check_results and os.path.exists(report_path):
                    try:
                        with open(report_path, newline="", encoding="utf-8") as f:
                            repair_rows = list(csv.DictReader(f))
                        if repair_rows:
                            rr = repair_rows[0]
                            info = self._check_results[src]
                            for k in ("final_size_bytes", "final_duration",
                                      "final_frozen_seconds", "verdict"):
                                if k in rr and rr[k]:
                                    info[k] = rr[k]
                    except Exception:
                        pass
            except Exception as e:
                self.root.after(0, self._log, f"[{i}/{total}] error: {e}\n")
        # Hide progress bar
        self.root.after(0, self.progress.pack_forget)
        rows = self._collect_reports(report_files)
        msg = f"Done: {ok}/{total} succeeded"
        self.root.after(0, self._done, msg, rows)

    def _collect_reports(self, report_files):
        """Read per-item CSV reports into a list of dict rows."""
        rows = []
        for rp in report_files:
            if not os.path.exists(rp):
                continue
            try:
                with open(rp, newline="", encoding="utf-8") as f:
                    rows.extend(list(csv.DictReader(f)))
            except Exception as e:
                self._log(f"(report read error: {e})\n")
        return rows

    def _done(self, msg, rows=None):
        self.running = False
        self.run_btn.config(state="normal")
        self.stop_btn.pack_forget()
        self._set_status(msg)
        self._log("\n" + msg + "\n")
        self._save_session()
        if rows:
            self._populate_results(rows, msg)

    def _populate_results(self, rows, msg):
        """Fill the Results tab with a summary table."""
        for child in list(self._results_frame.winfo_children()):
            child.destroy()

        hdr = tk.Frame(self._results_frame, bg=BG)
        hdr.pack(fill="x", padx=4, pady=(4, 2))
        tk.Label(hdr, text=msg, bg=BG, font=FONT_BOLD).pack(side="left")

        cols = [
            ("item", "#", 35), ("file", "Filename", 160),
            ("final_size_bytes", "Final Size", 70),
            ("final_duration", "Duration", 70),
        ]
        tree = ttk.Treeview(self._results_frame,
                            columns=[c[0] for c in cols],
                            show="headings", height=min(10, len(rows)))
        for key, label, width in cols:
            tree.heading(key, text=label)
            tree.column(key, width=width, anchor="center" if key != "file" else "w")

        sb = ttk.Scrollbar(self._results_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True, padx=4, pady=2)
        sb.pack(side="right", fill="y", pady=2)

        for idx, r in enumerate(rows, 1):
            sz = r.get("final_size_bytes", "-")
            if sz and sz != "-":
                try:
                    sz = self._human(int(sz))
                except (ValueError, TypeError):
                    pass
            dur = r.get("final_duration", "-")
            if dur and dur != "-":
                try:
                    m, s = divmod(int(float(dur)), 60)
                    h, m = divmod(m, 60)
                    dur = f"{h:02d}:{m:02d}:{s:02d}"
                except (ValueError, TypeError):
                    pass
            vals = [idx, os.path.basename(r.get("file", "?")), sz, dur]
            tree.insert("", "end", values=vals)

        self._notebook.select(3)  # switch to Results tab

    # ------------------------------------------------------------ popups
    def _human(self, n):
        """Bytes -> human readable."""
        try:
            n = float(n)
        except (TypeError, ValueError):
            return ""
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if abs(n) < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} PB"

        win = tk.Toplevel(self.root)
        win.title("Video-Fix-98 Report")
        win.configure(bg=BG)

        outer = tk.Frame(win, bg=BG, relief="raised", bd=2)
        outer.pack(padx=4, pady=4)

        header = tk.Label(outer, text=f"Run Report  -  {summary}", bg=NAVY,
                          fg=TITLE_FG, font=FONT_BOLD, anchor="w", padx=6, pady=3)
        header.pack(fill="x")

        cols = [
            ("file", "File", 160),
            ("size_bytes", "Size", 60),
            ("resolution", "Res", 70),
            ("video_codec", "Codec", 50),
            ("fps", "FPS", 40),
            ("audio_codec", "Audio", 45),
            ("claimed_duration", "Claimed (s)", 70),
            ("good_seconds", "Good (s)", 65),
            ("decodable_pct", "Decodable %", 60),
            ("error", "Error", 130),
            ("final_size_bytes", "Final Size", 70),
            ("final_duration", "Final (s)", 65),
            ("final_frozen_seconds", "Frozen (s)", 65),
            ("verdict", "Verdict", 70),
        ]
        tree = ttk.Treeview(outer, columns=[c[0] for c in cols],
                            show="headings", height=12)
        for key, label, width in cols:
            tree.heading(key, text=label)
            tree.column(key, width=width, anchor="w" if key == "file" else "center")

        vsb = ttk.Scrollbar(outer, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(outer, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        # pack a table container (keeps pack-only geometry on outer)
        table = tk.Frame(outer, bg=BG)
        table.pack(fill="both", expand=True, padx=4, pady=4)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")

        for r in rows:
            vals = []
            for key, _l, _w in cols:
                v = r.get(key, "")
                if key in ("size_bytes", "final_size_bytes"):
                    v = self._human(v)
                vals.append(v)
            tree.insert("", "end", values=vals)

        footer = tk.Frame(outer, bg=BG)
        footer.pack(fill="x", padx=4, pady=(0, 4))
        self.report_rows = rows
        tk.Button(footer, text="Export CSV...", command=self._export_report,
                  bg=BTNFACE, font=FONT, relief="raised", bd=2, padx=12,
                  pady=2).pack(side="left")
        tk.Button(footer, text="Close", command=win.destroy, bg=BTNFACE,
                  font=FONT, relief="raised", bd=2, padx=16, pady=2).pack(
            side="right")

        center_window(win, self.root)

    def _export_report(self):
        path = filedialog.asksaveasfilename(
            title="Export report CSV",
            defaultextension=".csv",
            initialfile="video-fix-98-report.csv",
            filetypes=[("CSV files", "*.csv")])
        if not path:
            return
        try:
            rows = getattr(self, "report_rows", [])
            if not rows:
                messagebox.showinfo("Video-Fix-98", "No report rows to export.")
                return
            header = list(rows[0].keys())
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            messagebox.showinfo("Video-Fix-98", f"Report exported:\n{path}")
        except Exception as e:
            messagebox.showerror("Video-Fix-98", f"Export failed: {e}")

    def _help(self):
        win = tk.Toplevel(self.root)
        win.title(f"Video-Fix-98 Help - v{VERSION}")
        win.configure(bg=BG)
        win.resizable(True, True)
        win.geometry("620x520")

        outer = tk.Frame(win, bg=BG, relief="raised", bd=2)
        outer.pack(fill="both", expand=True, padx=4, pady=4)

        header = tk.Label(outer, text=f"Video-Fix-98  v{VERSION}  -  Help",
                          bg=NAVY, fg=TITLE_FG, font=FONT_BOLD, anchor="w",
                          padx=6, pady=3)
        header.pack(fill="x")

        text = tk.Text(outer, bg=SUNKEN_BG, relief="sunken", bd=2,
                       font=("Courier", 9), wrap="word", padx=6, pady=4)
        sb = tk.Scrollbar(outer, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=sb.set)
        text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        for name, desc in VARIABLE_HELP:
            text.insert("end", f"\u2022 {name}:\n  {desc}\n\n")
        text.config(state="disabled")

        btn = tk.Button(outer, text="Close", command=win.destroy, bg=BTNFACE,
                        font=FONT, relief="raised", bd=2, padx=16, pady=2)
        btn.pack(pady=(0, 6))

        center_window(win, self.root)

    def _about(self):
        win = tk.Toplevel(self.root)
        win.title("About Video-Fix-98")
        win.configure(bg=BG)
        win.resizable(False, False)

        outer = tk.Frame(win, bg=BG, relief="raised", bd=2)
        outer.pack(padx=4, pady=4)

        header = tk.Label(outer, text="Video-Fix-98", bg=NAVY, fg=TITLE_FG,
                          font=(FONT_FAMILY, 12, "bold"), anchor="center",
                          padx=30, pady=4)
        header.pack(fill="x")

        logo = small_logo(self.root, width=120)
        if logo:
            lbl = tk.Label(outer, image=logo, bg=BG)
            lbl.image = logo
            lbl.pack(pady=6)
        else:
            tk.Label(outer, text="(logo unavailable)", bg=BG, fg="#000000",
                     font=FONT).pack(pady=6)

        tk.Label(outer, text=f"Version {VERSION}", bg=BG, fg="#000000",
                 font=FONT_BOLD).pack()
        tk.Label(outer, text="Corrupt video checker & repair.", bg=BG,
                 fg="#000000", font=FONT).pack(pady=(0, 2))
        tk.Label(outer, text="Created by SmoothMarx", bg=BG, fg="#000000",
                 font=FONT_BOLD).pack()

        thanks = tk.Label(outer,
                          text="Special thanks to:\n"
                               "  FFmpeg — the world's leading multimedia framework\n"
                               "  Untrunc (anthwlock fork of ponchio/untrunc) — moov rebuilds\n"
                               "  and the open-source community.",
                          bg=BG, fg="#000000", font=FONT, justify="center")
        thanks.pack(padx=12, pady=4)

        lic = tk.Label(outer,
                       text="License: free to use, modify and share.\n"
                            "Dependencies: FFmpeg (LGPL 2.1+ / GPL 2+) and\n"
                            "Untrunc (GPL-2.0).",
                       bg=BG, fg="#000000", font=FONT, justify="center")
        lic.pack(padx=12, pady=(0, 4))

        btn = tk.Button(outer, text="Close", command=win.destroy, bg=BTNFACE,
                        font=FONT, relief="raised", bd=2, padx=16, pady=2)
        btn.pack(pady=(0, 6))

        center_window(win, self.root)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-splash", action="store_true",
                    help="skip the splash screen")
    args = ap.parse_args()

    root = tk.Tk()
    if args.no_splash:
        SalvageGUI(root)
        root.mainloop()
    else:
        root.withdraw()
        show_splash(root, lambda: (root.deiconify(), SalvageGUI(root)))
        root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        crash_log = os.path.join(
            os.path.dirname(os.path.abspath(sys.executable)),
            "vf98-crash.log",
        )
        with open(crash_log, "w") as f:
            f.write("Video-Fix-98 crash report (runtime)\n")
            f.write("=" * 60 + "\n")
            traceback.print_exc(file=f)
            f.write("\n" + "=" * 60 + "\n")
            f.write(f"Python: {sys.version}\n")
            f.write(f"argv: {sys.argv}\n")
            f.write(f"_MEIPASS: {getattr(sys, '_MEIPASS', 'not frozen')}\n")
        sys.exit(1)
