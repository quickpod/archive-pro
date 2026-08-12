"""Deterministic, headless tests for the archivepro core library.

Everything runs in pytest ``tmp_path`` sandboxes; no network, no display, no
reliance on the host's file system layout.
"""

from __future__ import annotations

import os
import tarfile
import zipfile

import pytest

from archivepro import (
    ArchiveError,
    create,
    extract,
    list_contents,
)
from archivepro import test_archive as verify_archive


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
FILES = {
    "hello.txt": b"Hello, ArchivePro!\n" * 4,
    "data.bin": bytes(range(256)) * 8,
    os.path.join("sub", "nested.txt"): b"nested content \x00\x01\x02 end\n",
}


def make_tree(root):
    """Create a deterministic source tree under *root* and return its path."""
    src = os.path.join(root, "payload")
    os.makedirs(src, exist_ok=True)
    for rel, data in FILES.items():
        full = os.path.join(src, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data)
    return src


def assert_tree_matches(extracted_root):
    """Assert the tree extracted under ``<root>/payload`` matches FILES."""
    base = os.path.join(extracted_root, "payload")
    for rel, data in FILES.items():
        full = os.path.join(base, rel)
        assert os.path.isfile(full), f"missing extracted file: {rel}"
        with open(full, "rb") as fh:
            assert fh.read() == data, f"content mismatch: {rel}"


# --------------------------------------------------------------------------
# round-trip: multi-file formats
# --------------------------------------------------------------------------
@pytest.mark.parametrize("fmt,ext", [
    ("zip", ".zip"),
    ("7z", ".7z"),
    ("tar.gz", ".tar.gz"),
    ("tar.xz", ".tar.xz"),
])
def test_roundtrip_multifile(tmp_path, fmt, ext):
    src = make_tree(str(tmp_path))
    archive = str(tmp_path / ("out" + ext))
    report = create(archive, [src], fmt=fmt)
    assert report["added"] == len(FILES)
    assert report["format"] == fmt
    assert os.path.exists(archive)

    dest = str(tmp_path / "out_extracted")
    n = extract(archive, dest)
    assert n == len(FILES)
    assert_tree_matches(dest)


def test_roundtrip_7z_password(tmp_path):
    src = make_tree(str(tmp_path))
    archive = str(tmp_path / "secret.7z")
    create(archive, [src], password="s3cr3t")

    dest = str(tmp_path / "good")
    extract(archive, dest, password="s3cr3t")
    assert_tree_matches(dest)


def test_7z_wrong_password_raises(tmp_path):
    src = make_tree(str(tmp_path))
    archive = str(tmp_path / "secret.7z")
    create(archive, [src], password="s3cr3t")

    dest = str(tmp_path / "bad")
    with pytest.raises(ArchiveError):
        extract(archive, dest, password="wrong-one")


def test_roundtrip_zst_single_file(tmp_path):
    src_file = tmp_path / "blob.dat"
    payload = bytes(range(256)) * 40
    src_file.write_bytes(payload)
    archive = str(tmp_path / "blob.dat.zst")
    report = create(archive, [str(src_file)], fmt="zst")
    assert report["added"] == 1

    dest = str(tmp_path / "zst_out")
    extract(archive, dest)
    out = os.path.join(dest, "blob.dat")
    assert os.path.isfile(out)
    with open(out, "rb") as fh:
        assert fh.read() == payload


def test_roundtrip_gz_single_file(tmp_path):
    src_file = tmp_path / "notes.txt"
    payload = b"a single-file gzip stream\n" * 100
    src_file.write_bytes(payload)
    archive = str(tmp_path / "notes.txt.gz")
    create(archive, [str(src_file)], fmt="gz")

    dest = str(tmp_path / "gz_out")
    extract(archive, dest)
    with open(os.path.join(dest, "notes.txt"), "rb") as fh:
        assert fh.read() == payload


# --------------------------------------------------------------------------
# list_contents
# --------------------------------------------------------------------------
def test_list_contents_sizes(tmp_path):
    src = make_tree(str(tmp_path))
    archive = str(tmp_path / "listme.zip")
    create(archive, [src])

    entries = list_contents(archive)
    by_name = {e["name"].replace("\\", "/"): e for e in entries}
    for rel, data in FILES.items():
        key = "payload/" + rel.replace(os.sep, "/")
        assert key in by_name, f"{key} not listed; got {list(by_name)}"
        assert by_name[key]["size"] == len(data)
        assert by_name[key]["is_dir"] is False


# --------------------------------------------------------------------------
# integrity test
# --------------------------------------------------------------------------
def test_test_archive_good_and_truncated(tmp_path):
    src = make_tree(str(tmp_path))
    archive = tmp_path / "verify.zip"
    create(str(archive), [src])
    assert verify_archive(str(archive)) is True

    # Truncate to half its length -> the central directory is destroyed.
    raw = archive.read_bytes()
    truncated = tmp_path / "broken.zip"
    truncated.write_bytes(raw[: len(raw) // 2])
    assert verify_archive(str(truncated)) is False


def test_test_archive_truncated_7z(tmp_path):
    src = make_tree(str(tmp_path))
    archive = tmp_path / "verify.7z"
    create(str(archive), [src])
    assert verify_archive(str(archive)) is True
    raw = archive.read_bytes()
    broken = tmp_path / "broken.7z"
    broken.write_bytes(raw[: len(raw) // 2])
    assert verify_archive(str(broken)) is False


# --------------------------------------------------------------------------
# split -> rejoin
# --------------------------------------------------------------------------
def test_split_roundtrip(tmp_path):
    src = make_tree(str(tmp_path))
    archive = str(tmp_path / "big.7z")
    report = create(archive, [src], split_size=512)
    assert report["split"] is True
    assert len(report["parts"]) >= 2
    # The single combined file must not be left behind.
    assert not os.path.exists(archive)
    assert os.path.exists(archive + ".001")

    dest = str(tmp_path / "rejoined")
    extract(archive, dest)
    assert_tree_matches(dest)


def test_split_roundtrip_zip(tmp_path):
    src = make_tree(str(tmp_path))
    archive = str(tmp_path / "big.zip")
    create(archive, [src], split_size=400)
    dest = str(tmp_path / "rejoined_zip")
    extract(archive, dest)
    assert_tree_matches(dest)


# --------------------------------------------------------------------------
# PATH TRAVERSAL DEFENCE  (the important one)
# --------------------------------------------------------------------------
def test_zip_path_traversal_blocked(tmp_path):
    """A crafted '../evil' member must never be written outside dest."""
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(str(archive), "w") as zf:
        zf.writestr("../evil.txt", b"pwned")
        zf.writestr("ok.txt", b"fine")

    dest = tmp_path / "dest"
    dest.mkdir()
    sentinel = tmp_path / "evil.txt"  # would-be escape target (sibling of dest)

    with pytest.raises(ArchiveError):
        extract(str(archive), str(dest))

    assert not sentinel.exists(), "path traversal escaped the destination!"


def test_tar_path_traversal_blocked(tmp_path):
    archive = tmp_path / "evil.tar"
    with tarfile.open(str(archive), "w") as tf:
        info = tarfile.TarInfo(name="../evil.txt")
        payload = b"pwned"
        info.size = len(payload)
        import io
        tf.addfile(info, io.BytesIO(payload))

    dest = tmp_path / "dest"
    dest.mkdir()
    sentinel = tmp_path / "evil.txt"

    with pytest.raises(ArchiveError):
        extract(str(archive), str(dest))

    assert not sentinel.exists(), "tar path traversal escaped the destination!"


def test_absolute_path_member_blocked(tmp_path):
    archive = tmp_path / "abs.zip"
    with zipfile.ZipFile(str(archive), "w") as zf:
        zf.writestr("/tmp/archivepro_abs_escape.txt", b"nope")

    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(ArchiveError):
        extract(str(archive), str(dest))
    assert not os.path.exists("/tmp/archivepro_abs_escape.txt")


# --------------------------------------------------------------------------
# overwrite protection
# --------------------------------------------------------------------------
def test_overwrite_protection(tmp_path):
    src = make_tree(str(tmp_path))
    archive = str(tmp_path / "ov.zip")
    create(archive, [src])
    dest = str(tmp_path / "dest")
    extract(archive, dest)
    # Second extraction without overwrite must refuse.
    with pytest.raises(ArchiveError):
        extract(archive, dest)
    # With overwrite it succeeds.
    n = extract(archive, dest, overwrite=True)
    assert n == len(FILES)
