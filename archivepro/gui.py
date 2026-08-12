#!/usr/bin/env python3
r"""ArchivePro -- a pure-stdlib tkinter GUI on top of the ``archivepro`` API.

A single main window: a left sidebar (Browse, Create, Test) and a main panel
that swaps to the selected view.  Every operation calls the tested core library
(never re-implements archive logic) and runs on a background thread so the UI
stays responsive; results are marshalled back with ``self.after`` and reported
in a clear inline area -- a summary line on success, or the ``ArchiveError``
message (never a raw traceback) on failure.

Design goals baked in here:
  * pure standard-library tkinter/ttk -- NO third-party GUI deps.  Dark mode is
    a ttk-style + palette swap; "drag and drop" is an explicit "Add..." button.
  * Importing this module does nothing.  Only :func:`main` builds a root window,
    and it degrades gracefully (prints a message, returns 0) with no display.
  * Frozen-exe safe: bundled assets are resolved via ``sys._MEIPASS`` / the exe
    directory when ``sys.frozen`` is set -- never ``__file__``.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys
import threading

# NOTE: tkinter is imported lazily inside main()/build_app so that merely
# importing this module (e.g. during packaging or on a headless CI box) never
# fails.

APP_NAME = "ArchivePro"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "ArchivePro — by QuickOpen (quickopen.ai)"

ARCHIVE_TYPES = [
    ("Archives", "*.zip *.7z *.tar *.tar.gz *.tgz *.tar.bz2 *.tar.xz *.zst *.gz"),
    ("All files", "*.*"),
]

# Formats offered in the Create view (label -> extension).
CREATE_FORMATS = [
    ("ZIP (.zip)", "zip"),
    ("7z — encryptable (.7z)", "7z"),
    ("TAR (.tar)", "tar"),
    ("TAR + gzip (.tar.gz)", "tar.gz"),
    ("TAR + bzip2 (.tar.bz2)", "tar.bz2"),
    ("TAR + xz (.tar.xz)", "tar.xz"),
    ("Zstandard, single file (.zst)", "zst"),
    ("Gzip, single file (.gz)", "gz"),
]

# ---- colour palettes (mirror the QuickOpen palette) -------------------------
PALETTES = {
    "light": {
        "bg": "#f5f7fa", "surface": "#ffffff", "text": "#141820",
        "muted": "#5b6472", "primary": "#2f5fe0", "primary_hi": "#2450c8",
        "entry": "#ffffff", "border": "#d5dae2", "sel": "#2f5fe0",
        "sel_fg": "#ffffff", "trough": "#e2e7ef", "ok": "#1f7a3d",
        "err": "#c0392b",
    },
    "dark": {
        "bg": "#0f1115", "surface": "#1a1e24", "text": "#f1f3f7",
        "muted": "#9aa4b2", "primary": "#5b86f7", "primary_hi": "#7098ff",
        "entry": "#1a1e24", "border": "#2a2f38", "sel": "#5b86f7",
        "sel_fg": "#0f1115", "trough": "#2a2f38", "ok": "#5bd68a",
        "err": "#ff6b5e",
    },
}

VIEWS = [("browse", "Browse / Extract"), ("create", "Create"), ("test", "Test")]


# ---------------------------------------------------------------------------
# Asset / frozen handling
# ---------------------------------------------------------------------------
def asset_path(name):
    """Locate a bundled asset from source OR a PyInstaller one-file build.

    For a frozen exe we look only at ``sys._MEIPASS`` and the executable's own
    directory (never ``__file__``).  From source we also consult the package
    dir, the repo root and the CWD.  Returns an absolute path or ``None``.
    """
    roots = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(meipass)
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        roots += [here, os.path.dirname(here), os.getcwd()]
    for root in roots:
        candidate = os.path.join(root, name)
        if os.path.exists(candidate):
            return candidate
    return None


def human_size(num_bytes):
    """Human-readable byte size."""
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def open_in_file_manager(path):
    """Best-effort 'reveal in file manager', guarded on every platform."""
    try:
        folder = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
        if hasattr(os, "startfile"):          # Windows
            os.startfile(folder)              # noqa: S606 - intended
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", folder])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", folder])
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# The app (built lazily; tkinter imported only inside build_app/main)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to a live tkinter import.

    Kept inside a function so this module imports cleanly without a display.
    """
    import tkinter as tk
    from tkinter import ttk, filedialog

    from . import guiconfig
    from .errors import ArchiveError
    from .create import create
    from .extract import extract
    from .inspect import list_contents, test_archive

    FONT = "Segoe UI"

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(WINDOW_TITLE)
            self.geometry("1040x660")
            self.minsize(860, 540)

            self.theme = guiconfig.get_theme()
            self._busy = False
            self._img_refs = []
            self._tracked = []          # (widget, role) for manual re-theming
            self._current_archive = None
            self._create_sources = []   # list of paths to add

            self._set_icon()
            self._build_layout()
            self._apply_theme()
            self._select_view("browse")
            self._refresh_recent()
            self.protocol("WM_DELETE_WINDOW", self.destroy)

        # ---- assets / icon ------------------------------------------------
        def _set_icon(self):
            try:
                ico = asset_path("archive-pro.ico")
                if ico:
                    self.iconbitmap(ico)
                    return
            except Exception:
                pass
            try:
                png = asset_path("archive-pro.png")
                if png:
                    img = tk.PhotoImage(file=png)
                    self._img_refs.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass  # icon is cosmetic; never block launch

        def track(self, widget, role):
            self._tracked.append((widget, role))

        # ---- layout -------------------------------------------------------
        def _build_layout(self):
            self.columnconfigure(1, weight=1)
            self.rowconfigure(0, weight=1)

            side = ttk.Frame(self, style="Sidebar.TFrame", padding=(12, 14))
            side.grid(row=0, column=0, sticky="ns")
            ttk.Label(side, text=APP_NAME, style="Brand.TLabel").pack(anchor="w")
            ttk.Label(side, text="offline archiver", style="Status.TLabel").pack(
                anchor="w", pady=(0, 14))

            self._view_btns = {}
            for vid, label in VIEWS:
                b = ttk.Button(side, text=label, width=18,
                               command=lambda v=vid: self._select_view(v))
                b.pack(anchor="w", pady=3)
                self._view_btns[vid] = b

            ttk.Frame(side, height=18, style="Sidebar.TFrame").pack()
            self.theme_btn = ttk.Button(side, text="Toggle theme",
                                        command=self._toggle_theme, width=18)
            self.theme_btn.pack(anchor="w", pady=(6, 0), side="bottom")

            main = ttk.Frame(self, style="TFrame", padding=(16, 14))
            main.grid(row=0, column=1, sticky="nsew")
            main.columnconfigure(0, weight=1)
            main.rowconfigure(1, weight=1)

            head = ttk.Frame(main, style="TFrame")
            head.grid(row=0, column=0, sticky="ew")
            self.title_lbl = ttk.Label(head, text="", style="Header.TLabel")
            self.title_lbl.pack(anchor="w")
            self.desc_lbl = ttk.Label(head, text="", style="Sub.TLabel")
            self.desc_lbl.pack(anchor="w", pady=(2, 8))

            self.body = ttk.Frame(main, style="TFrame")
            self.body.grid(row=1, column=0, sticky="nsew")
            self.body.columnconfigure(0, weight=1)
            self.body.rowconfigure(0, weight=1)

            # inline status / result bar
            bar = ttk.Frame(main, style="Card.TFrame", padding=(10, 8))
            bar.grid(row=2, column=0, sticky="ew", pady=(10, 0))
            bar.columnconfigure(0, weight=1)
            self.result_lbl = ttk.Label(bar, text="Ready", style="Status.TLabel",
                                        anchor="w", wraplength=760, justify="left")
            self.result_lbl.grid(row=0, column=0, sticky="ew")
            self.openfolder_btn = ttk.Button(bar, text="Open folder",
                                             command=self._open_last_folder)
            self._last_output_dir = None

            self._panels = {}

        # ---- theming ------------------------------------------------------
        def _pal(self):
            return PALETTES[self.theme]

        def _toggle_theme(self):
            self.theme = "light" if self.theme == "dark" else "dark"
            guiconfig.set_theme(self.theme)
            self._apply_theme()

        def _apply_theme(self):
            p = self._pal()
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except Exception:
                pass
            self.configure(bg=p["bg"])
            style.configure(".", background=p["bg"], foreground=p["text"],
                            fieldbackground=p["entry"], bordercolor=p["border"],
                            font=(FONT, 10))
            style.configure("TFrame", background=p["bg"])
            style.configure("Sidebar.TFrame", background=p["surface"])
            style.configure("Card.TFrame", background=p["surface"])
            style.configure("TLabel", background=p["bg"], foreground=p["text"])
            style.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
            style.configure("Header.TLabel", background=p["bg"], foreground=p["text"],
                            font=(FONT, 15, "bold"))
            style.configure("Sub.TLabel", background=p["bg"], foreground=p["muted"])
            style.configure("Brand.TLabel", background=p["surface"],
                            foreground=p["text"], font=(FONT, 13, "bold"))
            style.configure("Status.TLabel", background=p["surface"],
                            foreground=p["muted"])
            style.configure("TButton", background=p["surface"], foreground=p["text"],
                            bordercolor=p["border"], focuscolor=p["surface"],
                            padding=(10, 5))
            style.map("TButton",
                      background=[("active", p["trough"]), ("pressed", p["trough"])])
            style.configure("Accent.TButton", background=p["primary"],
                            foreground=p["sel_fg"])
            style.map("Accent.TButton",
                      background=[("active", p["primary_hi"]),
                                  ("pressed", p["primary_hi"])])
            style.configure("TEntry", fieldbackground=p["entry"],
                            foreground=p["text"], bordercolor=p["border"])
            style.configure("TCombobox", fieldbackground=p["entry"],
                            foreground=p["text"])
            style.configure("Treeview", background=p["surface"],
                            fieldbackground=p["surface"], foreground=p["text"],
                            bordercolor=p["border"])
            style.map("Treeview", background=[("selected", p["sel"])],
                      foreground=[("selected", p["sel_fg"])])
            style.configure("Treeview.Heading", background=p["trough"],
                            foreground=p["text"])
            # re-theme raw tk widgets
            for w, role in self._tracked:
                try:
                    if role == "listbox":
                        w.configure(bg=p["entry"], fg=p["text"],
                                    selectbackground=p["sel"],
                                    selectforeground=p["sel_fg"],
                                    highlightbackground=p["border"])
                except Exception:
                    pass

        # ---- view switching ----------------------------------------------
        def _select_view(self, vid):
            for k, b in self._view_btns.items():
                b.state(["pressed"] if k == vid else ["!pressed"])
            for child in self.body.winfo_children():
                child.grid_forget()
            panel = self._panels.get(vid)
            if panel is None:
                builder = getattr(self, f"_panel_{vid}")
                panel = builder(self.body)
                self._panels[vid] = panel
                self._apply_theme()
            panel.grid(row=0, column=0, sticky="nsew")
            meta = {
                "browse": ("Browse / Extract",
                           "Open an archive to inspect its contents, then "
                           "extract everything or just the selection."),
                "create": ("Create archive",
                           "Add files and folders, choose a format and options, "
                           "then build. 7z supports a password (AES-256)."),
                "test": ("Test integrity",
                         "Verify that an archive (or split set) is complete and "
                         "not corrupt."),
            }[vid]
            self.title_lbl.configure(text=meta[0])
            self.desc_lbl.configure(text=meta[1])
            self._clear_result()

        # ---- Browse / Extract panel --------------------------------------
        def _panel_browse(self, master):
            f = ttk.Frame(master, style="TFrame")
            f.columnconfigure(0, weight=1)
            f.rowconfigure(2, weight=1)

            top = ttk.Frame(f, style="TFrame")
            top.grid(row=0, column=0, sticky="ew")
            ttk.Button(top, text="Open archive…", style="Accent.TButton",
                       command=self._browse_open).pack(side="left")
            ttk.Label(top, text="Recent:", style="Sub.TLabel").pack(
                side="left", padx=(12, 4))
            self.recent_var = tk.StringVar()
            self.recent_combo = ttk.Combobox(top, textvariable=self.recent_var,
                                              state="readonly", width=48)
            self.recent_combo.pack(side="left", fill="x", expand=True)
            self.recent_combo.bind("<<ComboboxSelected>>", self._open_recent)

            pw = ttk.Frame(f, style="TFrame")
            pw.grid(row=1, column=0, sticky="ew", pady=(8, 6))
            ttk.Label(pw, text="Password (if needed):",
                      style="Sub.TLabel").pack(side="left")
            self.browse_pw = tk.StringVar()
            ttk.Entry(pw, textvariable=self.browse_pw, show="•",
                      width=24).pack(side="left", padx=(6, 0))

            cols = ("size", "compressed")
            tree = ttk.Treeview(f, columns=cols, show="tree headings",
                                selectmode="extended")
            tree.heading("#0", text="Name")
            tree.heading("size", text="Size")
            tree.heading("compressed", text="Compressed")
            tree.column("#0", width=460)
            tree.column("size", width=110, anchor="e")
            tree.column("compressed", width=110, anchor="e")
            sb = ttk.Scrollbar(f, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.grid(row=2, column=0, sticky="nsew")
            sb.grid(row=2, column=1, sticky="ns")
            self.browse_tree = tree

            btns = ttk.Frame(f, style="TFrame")
            btns.grid(row=3, column=0, sticky="ew", pady=(8, 0))
            ttk.Button(btns, text="Extract all…", style="Accent.TButton",
                       command=lambda: self._do_extract(False)).pack(side="left")
            ttk.Button(btns, text="Extract selected…",
                       command=lambda: self._do_extract(True)).pack(
                side="left", padx=6)
            ttk.Button(btns, text="Test", command=self._do_test_current).pack(
                side="left")
            return f

        def _browse_open(self):
            path = filedialog.askopenfilename(title="Open archive",
                                              filetypes=ARCHIVE_TYPES)
            if path:
                self._load_archive(path)

        def _open_recent(self, _evt=None):
            path = self.recent_var.get()
            if path:
                self._load_archive(path)

        def _refresh_recent(self):
            try:
                recent = guiconfig.get_recent()
                self.recent_combo.configure(values=recent)
            except Exception:
                pass

        def _load_archive(self, path):
            if not os.path.exists(path) and not _looks_split(path):
                self._show_error(f"File not found: {path}")
                return
            pw = self.browse_pw.get() or None
            self._current_archive = path

            def work():
                return list_contents(path, password=pw)

            def ok(entries):
                self._populate_tree(entries)
                guiconfig.add_recent(path)
                self._refresh_recent()
                self.report_success(
                    f"Opened {os.path.basename(path)} — {len(entries)} entr"
                    f"{'y' if len(entries) == 1 else 'ies'}.")

            self._bg(work, ok, busy="Reading archive…")

        def _populate_tree(self, entries):
            tree = self.browse_tree
            tree.delete(*tree.get_children())
            for e in entries:
                name = e["name"]
                size = human_size(e["size"])
                comp = "-" if e["compressed"] is None else human_size(e["compressed"])
                tree.insert("", "end", iid=name,
                            text=name + ("/" if e["is_dir"] else ""),
                            values=(size, comp))

        def _do_extract(self, selected_only):
            if not self._current_archive:
                self._show_error("Open an archive first.")
                return
            members = None
            if selected_only:
                members = list(self.browse_tree.selection())
                if not members:
                    self._show_error("Select one or more entries to extract.")
                    return
            dest = filedialog.askdirectory(title="Extract into folder")
            if not dest:
                return
            pw = self.browse_pw.get() or None
            archive = self._current_archive

            def work():
                return extract(archive, dest, password=pw, members=members,
                               overwrite=True)

            def ok(n):
                self.report_success(
                    f"Extracted {n} file(s) to {dest}", outputs=[dest])

            self._bg(work, ok, busy="Extracting…")

        def _do_test_current(self):
            if not self._current_archive:
                self._show_error("Open an archive first.")
                return
            self._run_test(self._current_archive, self.browse_pw.get() or None)

        # ---- Create panel -------------------------------------------------
        def _panel_create(self, master):
            f = ttk.Frame(master, style="TFrame")
            f.columnconfigure(0, weight=1)
            f.rowconfigure(1, weight=1)

            add = ttk.Frame(f, style="TFrame")
            add.grid(row=0, column=0, sticky="ew")
            ttk.Button(add, text="Add files…", style="Accent.TButton",
                       command=self._add_files).pack(side="left")
            ttk.Button(add, text="Add folder…",
                       command=self._add_folder).pack(side="left", padx=6)
            ttk.Button(add, text="Remove", command=self._remove_source).pack(
                side="left")
            ttk.Button(add, text="Clear", command=self._clear_sources).pack(
                side="left", padx=6)

            lb_frame = ttk.Frame(f, style="TFrame")
            lb_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 8))
            self.src_list = tk.Listbox(lb_frame, height=8, activestyle="none",
                                       selectmode="extended", exportselection=False)
            sb = ttk.Scrollbar(lb_frame, orient="vertical",
                               command=self.src_list.yview)
            self.src_list.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            self.src_list.pack(side="left", fill="both", expand=True)
            self.track(self.src_list, "listbox")

            opts = ttk.Frame(f, style="TFrame")
            opts.grid(row=2, column=0, sticky="ew")
            ttk.Label(opts, text="Format:", style="Sub.TLabel").grid(
                row=0, column=0, sticky="w")
            self.fmt_var = tk.StringVar(value=CREATE_FORMATS[1][0])
            self.fmt_combo = ttk.Combobox(
                opts, textvariable=self.fmt_var, state="readonly", width=28,
                values=[lbl for lbl, _ in CREATE_FORMATS])
            self.fmt_combo.grid(row=0, column=1, sticky="w", padx=(6, 16))

            ttk.Label(opts, text="Level:", style="Sub.TLabel").grid(
                row=0, column=2, sticky="w")
            self.level_var = tk.StringVar()
            ttk.Entry(opts, textvariable=self.level_var, width=6).grid(
                row=0, column=3, sticky="w", padx=(6, 16))

            ttk.Label(opts, text="Password (7z):", style="Sub.TLabel").grid(
                row=1, column=0, sticky="w", pady=(8, 0))
            self.create_pw = tk.StringVar()
            ttk.Entry(opts, textvariable=self.create_pw, show="•", width=24).grid(
                row=1, column=1, sticky="w", padx=(6, 16), pady=(8, 0))

            ttk.Label(opts, text="Split (e.g. 10M):", style="Sub.TLabel").grid(
                row=1, column=2, sticky="w", pady=(8, 0))
            self.split_var = tk.StringVar()
            ttk.Entry(opts, textvariable=self.split_var, width=10).grid(
                row=1, column=3, sticky="w", padx=(6, 16), pady=(8, 0))

            out = ttk.Frame(f, style="TFrame")
            out.grid(row=3, column=0, sticky="ew", pady=(12, 0))
            out.columnconfigure(1, weight=1)
            ttk.Label(out, text="Save as:", style="Sub.TLabel").grid(
                row=0, column=0, sticky="w")
            self.out_var = tk.StringVar()
            ttk.Entry(out, textvariable=self.out_var).grid(
                row=0, column=1, sticky="ew", padx=6)
            ttk.Button(out, text="Browse…", command=self._pick_output).grid(
                row=0, column=2)
            ttk.Button(out, text="Build archive", style="Accent.TButton",
                       command=self._do_create).grid(row=0, column=3, padx=(8, 0))
            return f

        def _add_files(self):
            paths = filedialog.askopenfilenames(title="Add files")
            for p in paths:
                self.src_list.insert("end", p)

        def _add_folder(self):
            p = filedialog.askdirectory(title="Add folder")
            if p:
                self.src_list.insert("end", p)

        def _remove_source(self):
            for i in reversed(self.src_list.curselection()):
                self.src_list.delete(i)

        def _clear_sources(self):
            self.src_list.delete(0, "end")

        def _current_fmt(self):
            label = self.fmt_var.get()
            for lbl, fmt in CREATE_FORMATS:
                if lbl == label:
                    return fmt
            return None

        def _pick_output(self):
            fmt = self._current_fmt() or "zip"
            ext = "." + fmt
            p = filedialog.asksaveasfilename(title="Save archive as",
                                             defaultextension=ext,
                                             initialfile="archive" + ext)
            if p:
                self.out_var.set(p)

        def _do_create(self):
            sources = list(self.src_list.get(0, "end"))
            if not sources:
                self._show_error("Add at least one file or folder.")
                return
            out = self.out_var.get().strip()
            if not out:
                self._show_error("Choose an output path (Save as).")
                return
            fmt = self._current_fmt()
            level = self.level_var.get().strip()
            level = int(level) if level.isdigit() else None
            pw = self.create_pw.get() or None
            split = self.split_var.get().strip() or None
            from .__main__ import _parse_size

            def work():
                split_bytes = _parse_size(split) if split else None
                return create(out, sources, fmt=fmt, password=pw, level=level,
                              split_size=split_bytes)

            def ok(rep):
                ratio = rep["ratio"] * 100 if rep["total_size"] else 0
                msg = (f"Built {rep['format']} with {rep['added']} file(s): "
                       f"{human_size(rep['total_size'])} → "
                       f"{human_size(rep['compressed_size'])} ({ratio:.0f}%).")
                if rep.get("split"):
                    msg += f" Split into {len(rep['parts'])} volume(s)."
                self.report_success(msg, outputs=[out])

            self._bg(work, ok, busy="Building archive…")

        # ---- Test panel ---------------------------------------------------
        def _panel_test(self, master):
            f = ttk.Frame(master, style="TFrame")
            f.columnconfigure(1, weight=1)
            ttk.Label(f, text="Archive:", style="Sub.TLabel").grid(
                row=0, column=0, sticky="w")
            self.test_path = tk.StringVar()
            ttk.Entry(f, textvariable=self.test_path).grid(
                row=0, column=1, sticky="ew", padx=6)
            ttk.Button(f, text="Browse…", command=self._pick_test).grid(
                row=0, column=2)
            ttk.Label(f, text="Password:", style="Sub.TLabel").grid(
                row=1, column=0, sticky="w", pady=(8, 0))
            self.test_pw = tk.StringVar()
            ttk.Entry(f, textvariable=self.test_pw, show="•", width=24).grid(
                row=1, column=1, sticky="w", padx=6, pady=(8, 0))
            ttk.Button(f, text="Run integrity test", style="Accent.TButton",
                       command=self._do_test_panel).grid(
                row=2, column=1, sticky="w", pady=(12, 0))
            return f

        def _pick_test(self):
            p = filedialog.askopenfilename(title="Choose archive",
                                           filetypes=ARCHIVE_TYPES)
            if p:
                self.test_path.set(p)

        def _do_test_panel(self):
            path = self.test_path.get().strip()
            if not path:
                self._show_error("Choose an archive to test.")
                return
            self._run_test(path, self.test_pw.get() or None)

        def _run_test(self, path, pw):
            def work():
                return test_archive(path, password=pw)

            def ok(result):
                if result:
                    self.report_success(f"OK — {os.path.basename(path)} passed "
                                        f"the integrity check.")
                else:
                    self._show_error(f"{os.path.basename(path)} is corrupt or "
                                     f"incomplete.")

            self._bg(work, ok, busy="Testing…")

        # ---- background op runner ----------------------------------------
        def _bg(self, work, on_ok, busy="Working…"):
            """Run ``work()`` off the UI thread; call ``on_ok(result)`` back on it.

            Errors are shown inline (ArchiveError message, or a generic note),
            never as a traceback.  Refuses to start a second op while one runs.
            """
            if self._busy:
                self._show_error("Please wait — an operation is already running.")
                return
            self._busy = True
            self._set_status(busy, kind="working")

            def run():
                try:
                    res, err = work(), None
                except ArchiveError as ex:
                    res, err = None, str(ex)
                except Exception as ex:  # never leak a traceback
                    res, err = None, f"Unexpected error: {ex}"
                self.after(0, lambda: finish(res, err))

            def finish(res, err):
                self._busy = False
                if err is not None:
                    self._show_error(err)
                    return
                try:
                    on_ok(res)
                except Exception as ex:
                    self._show_error(f"Post-processing error: {ex}")

            threading.Thread(target=run, daemon=True).start()

        # ---- result bar helpers ------------------------------------------
        def _set_status(self, text, kind="idle"):
            p = self._pal()
            color = {"working": p["primary"], "ok": p["ok"], "err": p["err"]}.get(
                kind, p["muted"])
            self.result_lbl.configure(text=text, foreground=color)
            self.openfolder_btn.grid_forget()

        def _clear_result(self):
            self.result_lbl.configure(text="Ready", foreground=self._pal()["muted"])
            self.openfolder_btn.grid_forget()

        def _show_error(self, message):
            self.result_lbl.configure(text="✕ " + message,
                                      foreground=self._pal()["err"])
            self.openfolder_btn.grid_forget()

        def report_success(self, message, outputs=None):
            outputs = outputs or []
            for o in outputs:
                if o:
                    try:
                        guiconfig.add_recent(o)
                    except Exception:
                        pass
            if outputs:
                first = outputs[0]
                self._last_output_dir = (
                    first if os.path.isdir(first)
                    else os.path.dirname(os.path.abspath(first)))
                self.openfolder_btn.grid(row=0, column=1, sticky="e", padx=(8, 0))
            self.result_lbl.configure(text="✓ " + message,
                                      foreground=self._pal()["ok"])

        def _open_last_folder(self):
            if self._last_output_dir:
                open_in_file_manager(self._last_output_dir)

    def _looks_split(path):
        try:
            from . import split as _split
            return _split.is_split(path)
        except Exception:
            return False

    return App


def main():
    """Entry point: build the root window and run.  Degrades on headless hosts.

    Importing this module does nothing; only this function creates a Tk root.
    With no display (e.g. a server), it prints a friendly note and returns 0
    instead of raising.
    """
    if os.name != "nt" and not os.environ.get("DISPLAY") and sys.platform != "darwin":
        print(f"{APP_NAME}: no graphical display available — this GUI is meant "
              f"for the desktop. Use `python -m archivepro --help` for the CLI.")
        return 0

    try:
        import tkinter as tk
    except Exception as exc:  # tkinter missing entirely
        print(f"{APP_NAME}: a graphical environment with tkinter is required "
              f"to run the GUI ({exc}).")
        return 0

    try:
        App = build_app()
        app = App()
    except tk.TclError as exc:
        print(f"{APP_NAME}: no graphical display available — cannot start the "
              f"GUI here ({exc}). This app is intended for the desktop.")
        return 0
    except Exception as exc:
        print(f"{APP_NAME}: could not start the GUI ({exc}).")
        return 1

    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
