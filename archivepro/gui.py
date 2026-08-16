#!/usr/bin/env python3
r"""ArchivePro -- an Aura (QuickOpen design system) GUI on top of the
``archivepro`` API.

Layout per branding/aura-design-system/APP-LAYOUT-LANGUAGE.md, benchmarked
against 7-Zip's file-manager window (minus its pro tail):

  * **Sidebar** (AuraApp) -- Archive / Create / About nav plus a "Recent
    archives" library in ``sidebar_body`` (click to reopen).  Collapsible
    with Ctrl+\.
  * **Archive section** -- a 7-Zip-style toolbar (Open archive, Extract all,
    Extract selected, Test), a password field and a filter box on the right,
    over the contents table (Name / Size / Compressed / Modified).
    Right-click rows for Extract selected.  An Aura illustration fills the
    empty state before any archive is open.
  * **Create section** -- add files/folders, pick a format + options
    (7z AES-256 password, split volumes), build.  The output name is
    suggested from the first source.
  * **Status bar** -- entry counts + sizes; errors surface here, and an
    "Open output folder" action appears after successful operations.

A Ctrl+, Settings dialog offers the System/Light/Dark theme; fresh installs
follow the OS Aura theme live.  Every operation calls the tested core library
and runs on a background thread; failures show the ``ArchiveError`` message,
never a traceback.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys
import threading
import time

# NOTE: tkinter/customtkinter/aura are imported lazily inside build_app()/main()
# so that merely importing this module (e.g. during packaging or on a headless
# CI box, or without customtkinter installed) never fails.

APP_NAME = "ArchivePro"
APP_VERSION = "1.1.0"
WINDOW_TITLE = "ArchivePro — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai"
ACCENT = "#5b86f7"      # Aura brand accent (the old per-app orange was a
                        # legacy scaffold accent)

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


def rel_date(ts, now=None):
    """A compact human stamp: 'now', '5m', '2h', 'Yesterday', '12 Aug'."""
    if not ts:
        return ""
    now = now if now is not None else time.time()
    diff = max(0, now - ts)
    if diff < 90:
        return "now"
    if diff < 3600:
        return "%dm" % (diff // 60)
    if diff < 86400 and time.localtime(ts).tm_mday == time.localtime(now).tm_mday:
        return "%dh" % (diff // 3600)
    if diff < 2 * 86400:
        return "Yesterday"
    st, sn = time.localtime(ts), time.localtime(now)
    if st.tm_year == sn.tm_year:
        return time.strftime("%d %b", st)
    return time.strftime("%b %Y", st)


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
    import customtkinter as ctk

    from . import aura, guiconfig
    # archivepro/__init__ rebinds the package attributes 'create'/'extract'/
    # 'inspect' to their FUNCTIONS, so import the callables directly rather
    # than 'from . import create' (which would return the create() function).
    from .errors import ArchiveError
    from .create import create
    from .extract import extract
    from .inspect import list_contents, test_archive

    pair = aura._pair

    class App(aura.AuraApp):
        def __init__(self):
            super().__init__(
                title=WINDOW_TITLE, app_name=APP_NAME, accent=ACCENT,
                theme=guiconfig.get_theme(),
                icon_png=asset_path("archive-pro.png"), version=APP_VERSION,
                tagline="offline archiver",
                on_theme_change=guiconfig.set_theme,
                size=(1180, 720), min_size=(960, 600))

            self._busy = False
            self._img_refs_gui = []
            self._current_archive = None
            self._entries = []
            self._password = None       # for encrypted archives (Password…)
            self._last_output_dir = None

            self._set_icon()
            self._build_menu()
            # Persistent status-bar action; hidden until an op produces output.
            self._openfolder_btn = aura.AuraButton(
                self.statusbar.actions, "Open output folder", kind="secondary",
                height=30, command=self._open_last_folder)

            self.add_section("archive", "Archive", "▤", self._build_archive)
            self.add_section("create", "Create", "◈", self._build_create)
            self.add_section("about", "About", "ℹ", self._build_about)
            self._build_recent_sidebar()
            self.show("archive")
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

        # ---- menu + keyboard baseline (APP-LAYOUT-LANGUAGE.md §7/§9) ------
        def _build_menu(self):
            bar = tk.Menu(self)
            filem = tk.Menu(bar, tearoff=0)
            filem.add_command(label="Open archive…", accelerator="Ctrl+O",
                              command=self._open_archive_from_menu)
            filem.add_command(label="New archive", accelerator="Ctrl+N",
                              command=lambda: self.show("create"))
            filem.add_separator()
            filem.add_command(label="Settings…", accelerator="Ctrl+,",
                              command=self._open_settings)
            filem.add_separator()
            filem.add_command(label="Exit", command=self.destroy)
            bar.add_cascade(label="File", menu=filem)

            viewm = tk.Menu(bar, tearoff=0)
            viewm.add_command(label="Toggle sidebar", accelerator="Ctrl+\\",
                              command=self.toggle_sidebar)
            viewm.add_command(
                label="Toggle dark mode",
                command=lambda: self.set_theme(
                    "light" if self.theme == "dark" else "dark"))
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="About", command=lambda: self.show("about"))
            helpm.add_command(label="Open project page (quickopen.ai)",
                              command=lambda: open_with_default_app(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)

            self.bind_all("<Control-o>",
                          lambda e: (self._open_archive_from_menu(), "break")[1])
            self.bind_all("<Control-n>",
                          lambda e: (self.show("create"), "break")[1])
            self.bind_all("<Control-f>",
                          lambda e: (self._focus_filter(), "break")[1])
            self.bind_all("<Control-comma>",
                          lambda e: (self._open_settings(), "break")[1])

        def _open_archive_from_menu(self):
            self.show("archive")
            self._browse_open()

        def _focus_filter(self):
            try:
                self.show("archive")
                self.filter_entry.focus_set()
            except Exception:
                pass

        def _password_dialog(self):
            """Set (or clear) the password used for encrypted archives."""
            dlg = aura.Dialog(self, title="Archive password", size=(420, 210))
            aura.Caption(dlg.body,
                         "Used when opening, extracting or testing an "
                         "encrypted archive. Leave blank for none.").pack(
                anchor="w")
            entry = aura.AuraEntry(dlg.body, placeholder="password", show="•")
            entry.pack(fill="x", pady=(8, 0))
            if self._password:
                entry.insert(0, self._password)

            def ok(_e=None):
                self._password = entry.get() or None
                dlg.close()
                self.set_status("Password set." if self._password
                                else "Password cleared.")

            dlg.add_button("OK", ok)
            dlg.add_button("Cancel", dlg.close, kind="secondary")
            entry.bind("<Return>", ok)
            self.after(120, entry.focus_set)

        @staticmethod
        def _fill(entry, text):
            entry.delete(0, "end")
            if text:
                entry.insert(0, text)

        # =================================================================
        # Sidebar library: recent archives
        # =================================================================
        def _build_recent_sidebar(self):
            aura.SectionLabel(self.sidebar_body, "Recent archives").pack(
                anchor="w", padx=6, pady=(0, 4))
            self._recent_scroll = ctk.CTkScrollableFrame(
                self.sidebar_body, fg_color="transparent")
            self._recent_scroll.pack(fill="both", expand=True)
            self._refresh_recent()

        def _refresh_recent(self):
            for w in list(self._recent_scroll.winfo_children()):
                try:
                    w.destroy()
                except Exception:
                    pass
            recent = [p for p in guiconfig.get_recent()]
            if not recent:
                aura.Caption(self._recent_scroll,
                             "Archives you open appear here.").pack(
                    anchor="w", padx=6, pady=2)
                return
            for p in recent[:12]:
                active = (p == self._current_archive)
                btn = ctk.CTkButton(
                    self._recent_scroll, text=os.path.basename(p) or p,
                    anchor="w", height=30,
                    corner_radius=aura.TOKENS["geometry"]["radius_button"],
                    fg_color=pair("accent_soft") if active else "transparent",
                    hover_color=(aura._pal["light"]["surface2"],
                                 aura._pal["dark"]["surface2"]),
                    text_color=pair("text") if active else pair("muted"),
                    font=aura.font(role="body"),
                    command=lambda pp=p: self._load_archive(pp))
                btn.pack(fill="x", pady=1)
                aura.Tooltip(btn, p)

        # =================================================================
        # Archive section — toolbar + contents table (7-Zip layout)
        # =================================================================
        def _build_archive(self, frame):
            frame.grid_columnconfigure(0, weight=1)
            frame.grid_rowconfigure(1, weight=1)

            tb = aura.Toolbar(frame)
            tb.grid(row=0, column=0, sticky="ew", pady=(0, 10))
            tb.add_button("Open archive…", self._browse_open, kind="primary")
            tb.add_button("⤓ Extract all", lambda: self._do_extract(False))
            tb.add_button("Extract selected",
                          lambda: self._do_extract(True), kind="ghost")
            tb.add_separator()
            tb.add_button("✓ Test", self._do_test_current, kind="ghost")
            tb.add_button("Password…", self._password_dialog, kind="ghost",
                          tooltip="Password for encrypted archives")
            self.filter_entry = tb.add_search(
                "Filter entries…  (Ctrl+F)",
                on_change=lambda _t: self._populate_tree(), width=170)

            wrap = ctk.CTkFrame(frame, fg_color=pair("surface"),
                                corner_radius=10, border_width=1,
                                border_color=pair("border"))
            wrap.grid(row=1, column=0, sticky="nsew")
            cols = ("size", "compressed", "modified")
            tree = ttk.Treeview(wrap, columns=cols, show="tree headings",
                                selectmode="extended")
            tree.heading("#0", text="Name", anchor="w")
            tree.heading("size", text="Size", anchor="e")
            tree.heading("compressed", text="Compressed", anchor="e")
            tree.heading("modified", text="Modified", anchor="e")
            tree.column("#0", width=430, anchor="w")
            tree.column("size", width=100, minwidth=80, anchor="e",
                        stretch=False)
            tree.column("compressed", width=110, minwidth=90, anchor="e",
                        stretch=False)
            tree.column("modified", width=100, minwidth=80, anchor="e",
                        stretch=False)
            sb = aura.AuraScrollbar(wrap, command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y", padx=(0, 4), pady=6)
            tree.pack(side="left", fill="both", expand=True, padx=(6, 0),
                      pady=6)
            tree.bind("<Button-3>", self._show_entry_menu)
            self.browse_tree = tree
            self._entry_menu = tk.Menu(self, tearoff=0)
            aura.track(self._entry_menu, "menu")

            self.empty_archive = aura.EmptyState(
                frame, title="No archive open",
                caption="Open a ZIP, 7z, TAR (gz/bz2/xz), Zstandard or Gzip "
                        "file to inspect and extract it — or press Ctrl+N to "
                        "create a new archive.",
                action_text="Open archive…", action=self._browse_open,
                image=(asset_path("assets/archive-empty-light.png"),
                       asset_path("assets/archive-empty-dark.png")))
            self._update_empty_state()

        def _update_empty_state(self):
            if self._current_archive is None:
                self.empty_archive.place(relx=0, rely=0.06, relwidth=1,
                                         relheight=0.94)
                self.empty_archive.lift()
            else:
                self.empty_archive.place_forget()

        def _show_entry_menu(self, event):
            iid = self.browse_tree.identify_row(event.y)
            if not iid:
                return
            if iid not in self.browse_tree.selection():
                self.browse_tree.selection_set(iid)
            m = self._entry_menu
            m.delete(0, "end")
            m.add_command(label="Extract selected…",
                          command=lambda: self._do_extract(True))
            m.add_command(label="Extract all…",
                          command=lambda: self._do_extract(False))
            aura.style_menu(m)
            try:
                m.tk_popup(event.x_root, event.y_root)
            finally:
                try:
                    m.grab_release()
                except Exception:
                    pass

        def _browse_open(self):
            path = filedialog.askopenfilename(title="Open archive",
                                              filetypes=ARCHIVE_TYPES)
            if path:
                self._load_archive(path)

        def _load_archive(self, path):
            if not os.path.exists(path) and not _looks_split(path):
                self.set_error(f"File not found: {path}")
                return
            self.show("archive")
            pw = self._password

            def work():
                return list_contents(path, password=pw)

            def ok(entries):
                self._current_archive = path
                self._entries = entries
                try:
                    self.filter_entry.set("")
                except Exception:
                    pass
                self._populate_tree()
                guiconfig.add_recent(path)
                self._refresh_recent()
                self._update_empty_state()
                total = sum(e["size"] or 0 for e in entries)
                self.set_success(
                    f"{os.path.basename(path)} — {len(entries)} entr"
                    f"{'y' if len(entries) == 1 else 'ies'}, "
                    f"{human_size(total)} uncompressed.")

            self._bg(work, ok, busy="Reading archive…")

        def _populate_tree(self):
            tree = self.browse_tree
            tree.delete(*tree.get_children())
            q = ""
            try:
                q = self.filter_entry.get().strip().lower()
            except Exception:
                pass
            now = time.time()
            shown = 0
            for e in self._entries:
                name = e["name"]
                if q and q not in name.lower():
                    continue
                size = human_size(e["size"])
                comp = "-" if e["compressed"] is None else human_size(e["compressed"])
                mod = rel_date(e.get("modified"), now)
                tree.insert("", "end", iid=name,
                            text=name + ("/" if e["is_dir"] else ""),
                            values=(size, comp, mod))
                shown += 1
            if self._current_archive is not None:
                total = len(self._entries)
                if q:
                    self.set_status(f"{shown} of {total} entries match.")
                else:
                    self.set_status(f"{total} entr"
                                    f"{'y' if total == 1 else 'ies'}.")

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
            pw = self._password
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
            self._run_test(self._current_archive, self._password)

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
            aura.AuraButton(addrow, "＋ Add files…", kind="primary",
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
            sb = aura.AuraScrollbar(lbf, command=self.src_list.yview)
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

        def _suggest_output(self):
            """Prefill 'Save as' from the first source (7-Zip's 'Add' flow)."""
            if self.out_entry.get().strip():
                return
            items = self.src_list.get(0, "end")
            if not items:
                return
            first = items[0]
            base = os.path.basename(first.rstrip("/\\")) or "archive"
            root, _ = os.path.splitext(base)
            fmt = self._current_fmt() or "zip"
            self._fill(self.out_entry, os.path.join(
                os.path.dirname(first), (root or "archive") + "." + fmt))

        def _add_files(self):
            paths = filedialog.askopenfilenames(title="Add files")
            for p in paths:
                self.src_list.insert("end", p)
            if paths:
                self._suggest_output()

        def _add_folder(self):
            p = filedialog.askdirectory(title="Add folder")
            if p:
                self.src_list.insert("end", p)
                self._suggest_output()

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

        # ---- settings (Ctrl+,) --------------------------------------------
        def _open_settings(self):
            dlg = aura.Dialog(self, title="Settings", size=(520, 340))

            aura.SectionLabel(dlg.body, "Appearance").pack(anchor="w",
                                                           pady=(0, 2))
            trow = ctk.CTkFrame(dlg.body, fg_color="transparent")
            trow.pack(anchor="w", pady=(4, 2))
            aura.Caption(trow, "Theme").pack(side="left", padx=(0, 10))
            cur = guiconfig.get_theme()
            th = aura.AuraOption(trow, values=["System", "Light", "Dark"],
                                 width=110, height=30,
                                 command=self._set_theme_pref)
            th.set(cur.capitalize() if cur in ("light", "dark") else "System")
            th.pack(side="left")
            aura.Caption(dlg.body,
                         "System follows the OS Aura Dark/Light live.").pack(
                anchor="w", pady=(0, 14))

            aura.SectionLabel(dlg.body, "History").pack(anchor="w",
                                                        pady=(0, 2))
            hrow = ctk.CTkFrame(dlg.body, fg_color="transparent")
            hrow.pack(anchor="w", pady=(6, 0))
            aura.AuraButton(hrow, "Clear recent archives", kind="secondary",
                            height=30,
                            command=lambda: (guiconfig.clear_recent(),
                                             self._refresh_recent())).pack(
                side="left")

            dlg.add_button("Close")

        def _set_theme_pref(self, choice):
            pref = str(choice).lower()
            if pref == "system":
                guiconfig.set_theme("system")
                self._follow_system = True
                if self._sys_listener is None:
                    self._start_system_listener()
                self.set_theme(aura._system_theme(), _system=True)
            elif pref in ("light", "dark"):
                self.set_theme(pref)     # persists via on_theme_change

        # ---- theme: keep the sidebar library rows in sync ------------------
        def set_theme(self, theme, _system=False):
            super().set_theme(theme, _system=_system)
            try:
                self._refresh_recent()
            except Exception:
                pass

        # =================================================================
        # About section
        # =================================================================
        def _build_about(self, frame):
            card = aura.Card(frame, title="About ArchivePro")
            card.pack(fill="x")
            aura.Heading(card.body, APP_NAME).pack(anchor="w")
            aura.Caption(card.body, f"Version {APP_VERSION}").pack(
                anchor="w", pady=(0, 10))
            ctk.CTkLabel(
                card.body, font=aura.font(), justify="left", anchor="w",
                wraplength=560,
                text="A fast, fully-offline archiver — browse, extract, "
                     "create and test ZIP, 7z, TAR (gz/bz2/xz), Zstandard "
                     "and Gzip archives, with AES-256-encrypted 7z and "
                     "split volumes.\n\n"
                     "100% AI-built, open source, published on QuickOpen. "
                     "Nothing is ever uploaded anywhere.").pack(anchor="w")
            aura.Caption(card.body,
                         "Shortcuts: Ctrl+O open · Ctrl+N new archive · "
                         "Ctrl+F filter · Ctrl+, settings · Ctrl+\\ "
                         "sidebar").pack(anchor="w", pady=(10, 0))
            aura.Caption(card.body,
                         "Licensed under Apache-2.0. Built on py7zr, "
                         "zstandard and CustomTkinter (all permissive)."
                         ).pack(anchor="w", pady=(10, 4))
            aura.AuraButton(card.body, "Project page: quickopen.ai",
                            kind="ghost",
                            command=lambda: open_with_default_app(
                                PROJECT_URL)).pack(anchor="w", pady=(6, 0))

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
                try:
                    self.after(0, lambda: finish(res, err))
                except Exception:
                    self._busy = False

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
