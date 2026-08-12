"""Read-only inspection: list an archive's contents and test its integrity."""

from __future__ import annotations

import gzip
import os
import struct
import tarfile
import zipfile

from .errors import ArchiveError
from .formats import (
    detect_format,
    is_single_file_format,
    is_tar_format,
    single_file_output_name,
)
from . import split as _split


def _entry(name, size, compressed, modified, is_dir):
    return {
        "name": name,
        "size": int(size or 0),
        "compressed": None if compressed is None else int(compressed),
        "modified": modified,
        "is_dir": bool(is_dir),
    }


def _resolve_split(archive_path):
    """If *archive_path* is a split base, return (joined_temp, tmp_dir, fmt)."""
    if not os.path.exists(archive_path) and _split.is_split(archive_path):
        import shutil
        import tempfile

        tmp_dir = tempfile.mkdtemp(prefix="archivepro_ins_")
        joined = os.path.join(tmp_dir, os.path.basename(archive_path))
        try:
            fmt = _split.rejoin(archive_path, joined)
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
        return joined, tmp_dir, fmt or detect_format(joined)
    return None


def _list_zip(archive_path):
    out = []
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                mtime = None
                try:
                    import datetime

                    mtime = datetime.datetime(*info.date_time).timestamp()
                except Exception:
                    mtime = None
                out.append(_entry(info.filename, info.file_size,
                                  info.compress_size, mtime, info.is_dir()))
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"not a valid zip archive: {exc}") from exc
    return out


def _list_7z(archive_path, password):
    import py7zr

    out = []
    try:
        with py7zr.SevenZipFile(archive_path, "r", password=password) as zf:
            for fi in zf.list():
                mtime = None
                ct = getattr(fi, "creationtime", None)
                if ct is not None:
                    try:
                        mtime = ct.timestamp()
                    except Exception:
                        mtime = None
                out.append(_entry(
                    fi.filename,
                    getattr(fi, "uncompressed", 0),
                    getattr(fi, "compressed", None),
                    mtime,
                    getattr(fi, "is_directory", False),
                ))
    except Exception as exc:
        raise ArchiveError(
            f"failed to read 7z archive (wrong password or corrupt file): {exc}"
        ) from exc
    return out


def _list_tar(archive_path, fmt):
    from .formats import TAR_MODES

    mode = TAR_MODES[fmt]
    out = []
    try:
        with tarfile.open(archive_path, f"r:{mode}" if mode else "r:") as tf:
            for m in tf.getmembers():
                out.append(_entry(m.name, m.size,
                                  None if m.isdir() else m.size,
                                  m.mtime, m.isdir()))
    except tarfile.TarError as exc:
        raise ArchiveError(f"not a valid tar archive: {exc}") from exc
    return out


def _gz_uncompressed_size(archive_path):
    # gzip stores the original size mod 2**32 in the last 4 bytes (ISIZE).
    try:
        with open(archive_path, "rb") as fh:
            fh.seek(-4, os.SEEK_END)
            return struct.unpack("<I", fh.read(4))[0]
    except Exception:
        return 0


def _list_single(archive_path, fmt):
    inner = single_file_output_name(archive_path)
    compressed = os.path.getsize(archive_path)
    mtime = os.path.getmtime(archive_path)
    if fmt == "gz":
        size = _gz_uncompressed_size(archive_path)
    else:  # zst
        try:
            import zstandard as zstd

            with open(archive_path, "rb") as fh:
                size = zstd.frame_content_size(fh.read(18))
            if size in (-1, -2) or size is None:
                size = 0
        except Exception:
            size = 0
    return [_entry(inner, size, compressed, mtime, False)]


def list_contents(archive_path, password=None):
    """Return a list of entry dicts describing *archive_path*.

    Each entry has ``name``, ``size`` (uncompressed bytes), ``compressed``
    (bytes on disk, or ``None`` when not tracked per-member), ``modified``
    (POSIX timestamp or ``None``) and ``is_dir``.
    """
    archive_path = os.fspath(archive_path)
    resolved = _resolve_split(archive_path)
    if resolved:
        joined, tmp_dir, fmt = resolved
        import shutil

        try:
            return _list_for_format(joined, fmt, password)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if not os.path.exists(archive_path):
        raise ArchiveError(f"archive not found: {archive_path}")
    fmt = detect_format(archive_path)
    return _list_for_format(archive_path, fmt, password)


def _list_for_format(archive_path, fmt, password):
    if fmt == "zip":
        return _list_zip(archive_path)
    if fmt == "7z":
        return _list_7z(archive_path, password)
    if is_tar_format(fmt):
        return _list_tar(archive_path, fmt)
    if is_single_file_format(fmt):
        return _list_single(archive_path, fmt)
    raise ArchiveError(f"unsupported format: {fmt}")  # pragma: no cover


# --------------------------------------------------------------------------
# Integrity test
# --------------------------------------------------------------------------
def _test_zip(archive_path):
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            return zf.testzip() is None
    except (zipfile.BadZipFile, OSError, EOFError):
        return False


def _test_7z(archive_path, password):
    import py7zr

    try:
        with py7zr.SevenZipFile(archive_path, "r", password=password) as zf:
            result = zf.test()
    except Exception:
        # Unreadable/corrupt/wrong-password -> the archive did not verify.
        return False
    # test() returns True/False, or None when there are no CRCs to check.
    return result is None or bool(result)


def _test_tar(archive_path, fmt):
    from .formats import TAR_MODES

    mode = TAR_MODES[fmt]
    try:
        with tarfile.open(archive_path, f"r:{mode}" if mode else "r:") as tf:
            for m in tf.getmembers():
                if m.isfile():
                    src = tf.extractfile(m)
                    if src is None:
                        return False
                    with src:
                        while src.read(1024 * 1024):
                            pass
        return True
    except (tarfile.TarError, OSError, EOFError):
        return False


def _test_single(archive_path, fmt):
    try:
        if fmt == "gz":
            with gzip.open(archive_path, "rb") as fh:
                while fh.read(1024 * 1024):
                    pass
        else:  # zst
            import zstandard as zstd

            dctx = zstd.ZstdDecompressor()
            with open(archive_path, "rb") as fh:
                reader = dctx.stream_reader(fh)
                while reader.read(1024 * 1024):
                    pass
        return True
    except Exception:
        return False


def test_archive(archive_path, password=None):
    """Return ``True`` if *archive_path* is intact, ``False`` if corrupt.

    A wrong password (for an encrypted 7z) raises :class:`ArchiveError` rather
    than reporting the archive as corrupt.
    """
    archive_path = os.fspath(archive_path)
    resolved = _resolve_split(archive_path)
    if resolved:
        joined, tmp_dir, fmt = resolved
        import shutil

        try:
            return _test_for_format(joined, fmt, password)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if not os.path.exists(archive_path):
        raise ArchiveError(f"archive not found: {archive_path}")
    fmt = detect_format(archive_path)
    return _test_for_format(archive_path, fmt, password)


def _test_for_format(archive_path, fmt, password):
    if fmt == "zip":
        return _test_zip(archive_path)
    if fmt == "7z":
        return _test_7z(archive_path, password)
    if is_tar_format(fmt):
        return _test_tar(archive_path, fmt)
    if is_single_file_format(fmt):
        return _test_single(archive_path, fmt)
    raise ArchiveError(f"unsupported format: {fmt}")  # pragma: no cover


__all__ = ["list_contents", "test_archive"]
