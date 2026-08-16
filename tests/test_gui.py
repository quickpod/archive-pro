"""GUI tests for the 1.1.0 Aura layout-language rework (7-Zip benchmark).

Pure checks run anywhere; the App tests need a display (run the suite under
``xvfb-run -a python3 -m pytest``) and are skipped headless, mirroring the
house pattern.  Everything is hermetic via ARCHIVEPRO_HOME.
"""

import os
import sys
import time
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from archivepro import gui, guiconfig  # noqa: E402


def test_theme_defaults_to_system(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHIVEPRO_HOME", str(tmp_path))
    assert guiconfig.get_theme() == "system"
    guiconfig.set_theme("light")
    assert guiconfig.get_theme() == "light"
    guiconfig.set_theme("bogus")
    assert guiconfig.get_theme() == "light"
    guiconfig.set_theme("system")
    assert guiconfig.get_theme() == "system"


def test_rel_date_and_sizes():
    now = time.time()
    assert gui.rel_date(None) == ""
    assert gui.rel_date(now - 10, now) == "now"
    assert gui.human_size(0) == "0 B"
    assert gui.human_size(2048) == "2.0 KB"


# ---------------------------------------------------------------------------
# the real window (Xvfb)
# ---------------------------------------------------------------------------
needs_display = pytest.mark.skipif(
    sys.platform == "win32" or not os.environ.get("DISPLAY"),
    reason="needs a display (run under xvfb-run)")


def _pump(a, seconds=0.5):
    end = time.time() + seconds
    while time.time() < end:
        a.update()
        time.sleep(0.02)


def _wait_idle(a, timeout=15):
    deadline = time.time() + timeout
    while a._busy and time.time() < deadline:
        _pump(a, 0.1)


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    home = tmp_path_factory.mktemp("ap-home")
    old = os.environ.get("ARCHIVEPRO_HOME")
    os.environ["ARCHIVEPRO_HOME"] = str(home)
    App = gui.build_app()
    a = App()
    _pump(a, 0.8)

    # The house _bg marshals back with after(0, ...) from a worker thread,
    # which needs a running mainloop; tests pump update() instead, so run
    # operations synchronously here (same work/on_ok code paths).
    def sync_bg(work, on_ok, busy="Working…"):
        try:
            res = work()
        except Exception as ex:
            a.set_error(str(ex))
            return
        on_ok(res)
    a._bg = sync_bg
    yield a
    try:
        a.destroy()
    except Exception:
        pass
    if old is None:
        os.environ.pop("ARCHIVEPRO_HOME", None)
    else:
        os.environ["ARCHIVEPRO_HOME"] = old


@pytest.fixture()
def sample_zip(tmp_path):
    p = tmp_path / "sample.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("hello.txt", "hello world\n" * 10)
        z.writestr("docs/readme.md", "# readme\n")
        z.writestr("docs/deep/notes.txt", "notes\n")
    return str(p)


@needs_display
def test_shell_and_empty_state(app):
    assert app.active_section == "archive"
    assert app.sidebar_visible
    # empty state is placed before any archive opens
    assert app._current_archive is None
    app.toggle_sidebar()
    assert not app.sidebar_visible
    app.toggle_sidebar()


@needs_display
def test_open_filter_and_recents(app, sample_zip):
    app._load_archive(sample_zip)
    _wait_idle(app)
    _pump(app, 0.3)
    assert app._current_archive == sample_zip
    assert len(app.browse_tree.get_children()) == 3
    # filter narrows the table
    app.filter_entry.set("docs")
    app._populate_tree()
    assert len(app.browse_tree.get_children()) == 2
    app.filter_entry.set("")
    app._populate_tree()
    assert len(app.browse_tree.get_children()) == 3
    # recent library row appeared
    assert sample_zip in guiconfig.get_recent()


@needs_display
def test_extract_all(app, sample_zip, tmp_path, monkeypatch):
    from tkinter import filedialog
    dest = tmp_path / "out"
    dest.mkdir()
    app._load_archive(sample_zip)
    _wait_idle(app)
    monkeypatch.setattr(filedialog, "askdirectory", lambda **k: str(dest))
    app._do_extract(False)
    _wait_idle(app)
    _pump(app, 0.3)
    assert (dest / "hello.txt").exists()
    assert (dest / "docs" / "deep" / "notes.txt").exists()


@needs_display
def test_create_suggests_output_and_builds(app, tmp_path):
    src = tmp_path / "data.txt"
    src.write_text("x" * 4000)
    app.show("create")
    _pump(app, 0.3)
    app._clear_sources()
    app._fill(app.out_entry, "")
    app.src_list.insert("end", str(src))
    app._suggest_output()
    assert app.out_entry.get().endswith("data.7z")
    out = str(tmp_path / "built.zip")
    app.fmt_var.set("ZIP (.zip)")
    app._fill(app.out_entry, out)
    app._do_create()
    _wait_idle(app)
    _pump(app, 0.3)
    assert os.path.exists(out)
    with zipfile.ZipFile(out) as z:
        assert "data.txt" in z.namelist()


@needs_display
def test_test_verb_and_themes(app, sample_zip):
    app._load_archive(sample_zip)
    _wait_idle(app)
    app._do_test_current()
    _wait_idle(app)
    _pump(app, 0.3)
    for theme in ("light", "dark"):
        app.set_theme(theme)
        app.update_idletasks()
        app.update()
        assert app.theme == theme
