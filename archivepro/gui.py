#!/usr/bin/env python3
r"""ArchivePro -- an Aura (QuickOpen design system) GUI on top of the ``archivepro`` API.

A single Aura window with a sidebar of three sections: **Browse / Extract**
(open an archive, inspect its contents in a table, extract all or the
selection), **Create** (add files/folders, pick a format + options, build,
optionally encrypted 7z or split into volumes) and **Test** (verify an archive
or split set).  Every operation calls the tested core library (never
re-implements archive logic) and runs on a background thread so the UI stays
responsive; results are marshalled back with ``self.after`` and reported in the
Aura status bar -- a summary line on success, or the ``ArchiveError`` message
(never a raw traceback) on failure.

Design goals baked in here (mirrors the QuickOpen house style):
  * built on the vendored ``archivepro/aura.py`` design system, which layers the
    quickopen.ai look (deep space + light) over CustomTkinter.  Runtime deps:
    ``customtkinter`` (+ ``darkdetect``) -- declared in requirements.txt; the
    PyInstaller build adds ``--collect-all customtkinter``.
  * Importing this module does nothing.  Only :func:`main` builds a root
    window, and it degrades gracefully (prints a message, returns 0) with no
    display or with customtkinter missing.
  * Frozen-exe safe: bundled assets are resolved via ``sys._MEIPASS`` / the exe
    directory when ``sys.frozen`` is set -- never ``__file__``.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys
import threading

# NOTE: tkinter/customtkinter/aura are imported lazily inside build_app()/main()
# so that merely importing this module (e.g. during packaging or on a headless
# CI box, or without customtkinter installed) never fails.

APP_NAME = "ArchivePro"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "ArchivePro — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai"
ACCENT = "#b0700a"      # publish/specs/archive-pro.json "accent": [176, 112, 10]

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


# ---------------------------------------------------------------------------
# Asset / frozen handling  +  small OS helpers
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


def open_with_default_app(path):
    """Open a file/URL with the OS default application, guarded."""
    try:
        if hasattr(os, "startfile"):
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# The app (built lazily; tkinter/customtkinter imported only inside build_app)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to live GUI imports.

    Kept inside a function so this module imports cleanly without a display
    (and without customtkinter installed).
    """
    import tkinter as tk
    from tkinter import ttk, filedialog
    import customtkinter as ctk  # noqa: F401 - imported so ImportError surfaces here

    from . import aura, guiconfig
    # archivepro/__init__ rebinds the package attributes 'create'/'extract'/
    # 'inspect' to their FUNCTIONS, so import the callables directly rather
    # than 'from . import create' (which would return the create() function).
    from .errors import ArchiveError
    from .create import create
    from .extract import extract
    from .inspect import list_contents, test_archive

    class App(aura.AuraApp):
        def __init__(self):
            super().__init__(
                title=WINDOW_TITLE, app_name=APP_NAME, accent=ACCENT,
                theme=guiconfig.get_theme(),
                icon_png=asset_path("archive-pro.png"), version=APP_VERSION,
                tagline="offline archiver",
                on_theme_change=guiconfig.set_theme,
                size=(1040, 720), min_size=(880, 600))

            self._busy = False
            self._img_refs_gui = []
            self._current_archive = None
            self._last_output_dir = None

            self._set_icon()
            self._build_menu()
            # Persistent status-bar action; hidden until an op produces output.
            self._openfolder_btn = aura.AuraButton(
                self.statusbar.actions, "Open output folder", kind="secondary",
                height=30, command=self._open_last_folder)

            self.add_section("browse", "Browse / Extract", "▤", self._build_browse)
            self.add_section("create", "Create", "◈", self._build_create)
            self.add_section("test", "Test", "✳", self._build_test)
            self.show("browse")
            self._refresh_recent()
            self.set_status("Ready")
            self.protocol("WM_DELETE_WINDOW", self.destroy)

        # ---- assets / icon ------------------------------------------------
        def _set_icon(self):
            try:
                ico = asset_path("archive-pro.ico")
                if ico and os.name == "nt":
                    self.iconbitmap(ico)
                    return
            except Exception:
                pass
            try:
                png = asset_path("archive-pro.png")
                if png:
                    img = tk.PhotoImage(file=png)
                    self._img_refs_gui.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass  # icon is cosmetic; never block launch

        # ---- menu (native menus stay; theme lives in the sidebar toggle too)
        def _build_menu(self):
            bar = tk.Menu(self)
            filem = tk.Menu(bar, tearoff=0)
            filem.add_command(label="Open archive…",
                              command=self._open_archive_from_menu)
            filem.add_separator()
            filem.add_command(label="Exit", command=self.destroy)
            bar.add_cascade(label="File", menu=filem)

            viewm = tk.Menu(bar, tearoff=0)
            viewm.add_command(
                label="Toggle dark mode",
                command=lambda: self.set_theme(
                    "light" if self.theme == "dark" else "dark"))
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="Open project page (quickopen.ai)",
                              command=lambda: open_with_default_app(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)

        def _open_archive_from_menu(self):
            self.show("browse")
            self._browse_open()

        @staticmethod
        def _fill(entry, text):
            entry.delete(0, "end")
            if text:
                entry.insert(0, text)

        # =================================================================
        # Browse / Extract section
        # =================================================================
        def _build_browse(self, frame):
            aura.Caption(
                frame,
                "Open an archive to inspect its contents, then extract "
                "everything or just the selection.").pack(anchor="w",
                                                          pady=(0, 12))

            top = ctk.CTkFrame(frame, fg_color="transparent")
            top.pack(fill="x")
            aura.AuraButton(top, "Open archive…", kind="primary",
                            command=self._browse_open).pack(side="left")
            aura.Caption(top, "Recent").pack(side="left", padx=(14, 6))
            self.recent_var = tk.StringVar()
            self.recent_combo = aura.AuraCombo(
                top, variable=self.recent_var, values=[], state="readonly",
                command=self._open_recent)
            self.recent_combo.pack(side="left", fill="x", expand=True)

            pw = ctk.CTkFrame(frame, fg_color="transparent")
            pw.pack(fill="x", pady=(10, 8))
            aura.Caption(pw, "Password (if needed)").pack(side="left",
                                                          padx=(0, 8))
            self.browse_pw = aura.AuraEntry(pw, placeholder="•••", show="•",
                                            width=200)
            self.browse_pw.pack(side="left")

            body = ctk.CTkFrame(frame, fg_color="transparent")
            body.pack(fill="both", expand=True)
            cols = ("size", "compressed")
            tree = ttk.Treeview(body, columns=cols, show="tree headings",
                                selectmode="extended")
            tree.heading("#0", text=aura.spaced("Name"), anchor="w")
            tree.heading("size", text=aura.spaced("Size"), anchor="e")
            tree.heading("compressed", text=aura.spaced("Compressed"),
                         anchor="e")
            tree.column("#0", width=460, anchor="w")
            tree.column("size", width=110, anchor="e")
            tree.column("compressed", width=120, anchor="e")
            sb = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            tree.pack(side="left", fill="both", expand=True)
            self.browse_tree = tree

            btns = ctk.CTkFrame(frame, fg_color="transparent")
            btns.pack(fill="x", pady=(12, 0))
            aura.AuraButton(btns, "Extract all…", kind="primary",
                            command=lambda: self._do_extract(False)).pack(
                side="left")
            aura.AuraButton(btns, "Extract selected…", kind="secondary",
                            command=lambda: self._do_extract(True)).pack(
                side="left", padx=8)
            aura.AuraButton(btns, "Test", kind="secondary",
                            command=self._do_test_current).pack(side="left")

        def _browse_open(self):
            path = filedialog.askopenfilename(title="Open archive",
                                              filetypes=ARCHIVE_TYPES)
            if path:
                self._load_archive(path)

        def _open_recent(self, value=None):
            path = value if isinstance(value, str) and value \
                else self.recent_var.get()
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
                self.set_error(f"File not found: {path}")
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
                self.set_error("Open an archive first.")
                return
            members = None
            if selected_only:
                members = list(self.browse_tree.selection())
                if not members:
                    self.set_error("Select one or more entries to extract.")
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
                self.set_error("Open an archive first.")
                return
            self._run_test(self._current_archive, self.browse_pw.get() or None)

        # =================================================================
        # Create section
        # =================================================================
        def _build_create(self, frame):
            aura.Caption(
                frame,
                "Add files and folders, choose a format and options, then "
                "build. 7z supports a password (AES-256).").pack(
                anchor="w", pady=(0, 12))

            files = aura.Card(frame, title="Files & folders")
            files.pack(fill="both", expand=True, pady=(0, 12))
            addrow = ctk.CTkFrame(files.body, fg_color="transparent")
            addrow.pack(fill="x", pady=(0, 8))
            aura.AuraButton(addrow, "Add files…", kind="primary",
                            command=self._add_files).pack(side="left")
            aura.AuraButton(addrow, "Add folder…", kind="secondary",
                            command=self._add_folder).pack(side="left", padx=8)
            aura.AuraButton(addrow, "Remove", kind="secondary",
                            command=self._remove_source).pack(side="left")
            aura.AuraButton(addrow, "Clear", kind="ghost",
                            command=self._clear_sources).pack(side="left",
                                                              padx=8)
            lbf = ctk.CTkFrame(files.body, fg_color="transparent")
            lbf.pack(fill="both", expand=True)
            self.src_list = tk.Listbox(lbf, height=6, activestyle="none",
                                       selectmode="extended",
                                       exportselection=False)
            sb = ttk.Scrollbar(lbf, orient="vertical",
                               command=self.src_list.yview)
            self.src_list.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            self.src_list.pack(side="left", fill="both", expand=True)
            aura.track(self.src_list, "listbox")

            opts = aura.Card(frame, title="Options")
            opts.pack(fill="x", pady=(0, 12))
            og = ctk.CTkFrame(opts.body, fg_color="transparent")
            og.pack(fill="x")
            aura.Caption(og, "Format").grid(row=0, column=0, sticky="w",
                                            padx=(0, 8), pady=(0, 8))
            self.fmt_var = tk.StringVar(value=CREATE_FORMATS[1][0])
            self.fmt_combo = aura.AuraCombo(
                og, variable=self.fmt_var, state="readonly", width=240,
                values=[lbl for lbl, _ in CREATE_FORMATS])
            self.fmt_combo.grid(row=0, column=1, sticky="w", padx=(0, 20),
                                pady=(0, 8))
            aura.Caption(og, "Level").grid(row=0, column=2, sticky="w",
                                           padx=(0, 8), pady=(0, 8))
            self.level_entry = aura.AuraEntry(og, placeholder="auto", width=90)
            self.level_entry.grid(row=0, column=3, sticky="w", pady=(0, 8))

            aura.Caption(og, "Password (7z)").grid(row=1, column=0, sticky="w",
                                                   padx=(0, 8))
            self.create_pw = aura.AuraEntry(og, show="•", placeholder="optional",
                                            width=240)
            self.create_pw.grid(row=1, column=1, sticky="w", padx=(0, 20))
            aura.Caption(og, "Split (e.g. 10M)").grid(row=1, column=2,
                                                      sticky="w", padx=(0, 8))
            self.split_entry = aura.AuraEntry(og, placeholder="e.g. 10M",
                                              width=120)
            self.split_entry.grid(row=1, column=3, sticky="w")

            out = aura.Card(frame, title="Output")
            out.pack(fill="x")
            outg = ctk.CTkFrame(out.body, fg_color="transparent")
            outg.pack(fill="x")
            outg.grid_columnconfigure(1, weight=1)
            aura.Caption(outg, "Save as").grid(row=0, column=0, sticky="w",
                                               padx=(0, 8))
            self.out_entry = aura.AuraEntry(outg, placeholder="Save archive as…")
            self.out_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
            aura.AuraButton(outg, "Browse…", kind="secondary",
                            command=self._pick_output).grid(row=0, column=2,
                                                            padx=(0, 8))
            aura.AuraButton(outg, "Build archive", kind="primary",
                            command=self._do_create).grid(row=0, column=3)

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
                self._fill(self.out_entry, p)

        def _do_create(self):
            sources = list(self.src_list.get(0, "end"))
            if not sources:
                self.set_error("Add at least one file or folder.")
                return
            out = self.out_entry.get().strip()
            if not out:
                self.set_error("Choose an output path (Save as).")
                return
            fmt = self._current_fmt()
            level = self.level_entry.get().strip()
            level = int(level) if level.isdigit() else None
            pw = self.create_pw.get() or None
            split = self.split_entry.get().strip() or None
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

        # =================================================================
        # Test section
        # =================================================================
        def _build_test(self, frame):
            aura.Caption(
                frame,
                "Verify that an archive (or split set) is complete and not "
                "corrupt.").pack(anchor="w", pady=(0, 12))

            card = aura.Card(frame, title="Test integrity")
            card.pack(fill="x")
            g = ctk.CTkFrame(card.body, fg_color="transparent")
            g.pack(fill="x")
            g.grid_columnconfigure(1, weight=1)
            aura.Caption(g, "Archive").grid(row=0, column=0, sticky="w",
                                            padx=(0, 8), pady=(0, 8))
            self.test_path = aura.AuraEntry(g, placeholder="Archive to test…")
            self.test_path.grid(row=0, column=1, sticky="ew", padx=(0, 8),
                                pady=(0, 8))
            aura.AuraButton(g, "Browse…", kind="secondary",
                            command=self._pick_test).grid(row=0, column=2,
                                                          pady=(0, 8))
            aura.Caption(g, "Password").grid(row=1, column=0, sticky="w",
                                             padx=(0, 8))
            self.test_pw = aura.AuraEntry(g, show="•", placeholder="optional",
                                          width=220)
            self.test_pw.grid(row=1, column=1, sticky="w")
            aura.AuraButton(card.body, "Run integrity test", kind="primary",
                            command=self._do_test_panel).pack(anchor="w",
                                                              pady=(14, 0))

        def _pick_test(self):
            p = filedialog.askopenfilename(title="Choose archive",
                                           filetypes=ARCHIVE_TYPES)
            if p:
                self._fill(self.test_path, p)

        def _do_test_panel(self):
            path = self.test_path.get().strip()
            if not path:
                self.set_error("Choose an archive to test.")
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
                    self.set_error(f"{os.path.basename(path)} is corrupt or "
                                   f"incomplete.")

            self._bg(work, ok, busy="Testing…")

        # =================================================================
        # Background op runner + status helpers
        # =================================================================
        def _bg(self, work, on_ok, busy="Working…"):
            """Run ``work()`` off the UI thread; call ``on_ok(result)`` back on it.

            Errors are shown inline in the Aura status bar (ArchiveError
            message, or a generic note), never as a traceback.  Refuses to
            start a second op while one runs.
            """
            if self._busy:
                self.set_error("Please wait — an operation is already running.")
                return
            self._busy = True
            self._openfolder_btn.pack_forget()
            self.set_status(busy, kind="working")

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
                    self.set_error(err)
                    return
                try:
                    on_ok(res)
                except Exception as ex:
                    self.set_error(f"Post-processing error: {ex}")

            threading.Thread(target=run, daemon=True).start()

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
                self._openfolder_btn.pack(side="left")
            self.set_success(message)

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
    With no display (e.g. a server) or without customtkinter installed, it
    prints a friendly note and returns 0 instead of raising.
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
    except ImportError as exc:
        print(f"{APP_NAME}: the GUI needs the 'customtkinter' package "
              f"({exc}). Install it with:  pip install customtkinter")
        return 0
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
