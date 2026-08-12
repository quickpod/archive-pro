"""Create archives in any supported format.

The single public entry point is :func:`create`.  It never re-implements
compression -- it drives the standard library (``zipfile``/``tarfile``/``gzip``)
plus ``py7zr`` and ``zstandard`` -- and always reports what it produced.
"""

from __future__ import annotations

import gzip
import os
import shutil
import tarfile
import tempfile
import zipfile

from .errors import ArchiveError
from .formats import (
    TAR_MODES,
    format_from_extension,
    is_single_file_format,
    is_tar_format,
    normalize_format,
    single_file_output_name,
)
from .split import SPLIT_MANIFEST_SUFFIX, write_split


def _resolve_format(archive_path, fmt):
    fmt = normalize_format(fmt)
    if fmt:
        return fmt
    fmt = format_from_extension(archive_path)
    if fmt:
        return fmt
    raise ArchiveError(
        "could not infer archive format from the file name; pass an explicit "
        "format (e.g. fmt='zip')"
    )


def _collect(sources):
    """Yield ``(fullpath, arcname, is_dir)`` for every member of *sources*.

    Each top-level source keeps its basename as the archive root.  Directories
    are walked recursively; empty directories are preserved as explicit
    entries.  Missing sources raise :class:`ArchiveError`.
    """
    if isinstance(sources, (str, bytes, os.PathLike)):
        sources = [sources]
    sources = [os.fspath(s) for s in sources]
    if not sources:
        raise ArchiveError("no input files or folders were given")

    for src in sources:
        if not os.path.exists(src):
            raise ArchiveError(f"source not found: {src}")
        base = os.path.basename(os.path.normpath(src))
        if os.path.isdir(src):
            yield src, base, True
            for root, dirs, files in os.walk(src):
                dirs.sort()
                rel_root = os.path.relpath(root, src)
                for d in sorted(dirs):
                    full = os.path.join(root, d)
                    arc = os.path.normpath(os.path.join(base, rel_root, d))
                    if not os.listdir(full):
                        yield full, arc, True
                for f in sorted(files):
                    full = os.path.join(root, f)
                    arc = os.path.normpath(os.path.join(base, rel_root, f))
                    yield full, arc, False
        else:
            yield src, base, False


def _arcname(arc):
    # Normalise separators to forward slashes for portable archives.
    return arc.replace(os.sep, "/")


def _create_zip(archive_path, entries, password, level):
    if password:
        raise ArchiveError(
            "the ZIP writer in the Python standard library cannot encrypt; "
            "use the 7z format for a password-protected archive"
        )
    kwargs = {}
    if level is not None:
        kwargs["compresslevel"] = int(level)
    added = 0
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED, **kwargs
    ) as zf:
        for full, arc, is_dir in entries:
            name = _arcname(arc)
            if is_dir:
                zf.writestr(name.rstrip("/") + "/", b"")
            else:
                zf.write(full, name)
                added += 1
    return added


def _create_7z(archive_path, entries, password, level):
    import py7zr

    filters = None
    if level is not None:
        try:
            filters = [{"id": py7zr.FILTER_LZMA2, "preset": int(level)}]
        except Exception:  # pragma: no cover - defensive
            filters = None
    open_kwargs = {"mode": "w"}
    if password:
        open_kwargs["password"] = password
        open_kwargs["header_encryption"] = True
    if filters:
        open_kwargs["filters"] = filters
    added = 0
    try:
        with py7zr.SevenZipFile(archive_path, **open_kwargs) as zf:
            for full, arc, is_dir in entries:
                zf.write(full, _arcname(arc))
                if not is_dir:
                    added += 1
    except ArchiveError:
        raise
    except Exception as exc:
        raise ArchiveError(f"failed to create 7z archive: {exc}") from exc
    return added


def _create_tar(archive_path, fmt, entries, password, level):
    if password:
        raise ArchiveError(
            "tar archives cannot be password-protected; use the 7z format"
        )
    mode = TAR_MODES[fmt]
    open_kwargs = {}
    if level is not None:
        if mode in ("gz", "bz2"):
            open_kwargs["compresslevel"] = int(level)
        elif mode == "xz":
            open_kwargs["preset"] = int(level)
    added = 0
    try:
        with tarfile.open(archive_path, f"w:{mode}" if mode else "w",
                          **open_kwargs) as tf:
            for full, arc, is_dir in entries:
                tf.add(full, arcname=_arcname(arc), recursive=False)
                if not is_dir:
                    added += 1
    except tarfile.TarError as exc:
        raise ArchiveError(f"failed to create tar archive: {exc}") from exc
    return added


def _create_single_file(archive_path, fmt, entries, password, level):
    if password:
        raise ArchiveError(
            f"{fmt} single-file archives cannot be password-protected; use 7z"
        )
    files = [(full, arc) for full, arc, is_dir in entries if not is_dir]
    if len(files) != 1:
        raise ArchiveError(
            f"the {fmt} format stores exactly one file; got {len(files)}. "
            f"Bundle multiple files with a tar-based or zip/7z format first."
        )
    src = files[0][0]
    if fmt == "gz":
        lvl = 9 if level is None else int(level)
        with open(src, "rb") as fin, gzip.open(archive_path, "wb",
                                                compresslevel=lvl) as fout:
            shutil.copyfileobj(fin, fout)
    else:  # zst
        import zstandard as zstd

        lvl = 3 if level is None else int(level)
        cctx = zstd.ZstdCompressor(level=lvl)
        with open(src, "rb") as fin, open(archive_path, "wb") as fout:
            cctx.copy_stream(fin, fout)
    return 1


def _build(archive_path, fmt, entries, password, level):
    if fmt == "zip":
        return _create_zip(archive_path, entries, password, level)
    if fmt == "7z":
        return _create_7z(archive_path, entries, password, level)
    if is_tar_format(fmt):
        return _create_tar(archive_path, fmt, entries, password, level)
    if is_single_file_format(fmt):
        return _create_single_file(archive_path, fmt, entries, password, level)
    raise ArchiveError(f"unsupported format: {fmt}")  # pragma: no cover


def create(archive_path, sources, fmt=None, password=None, level=None,
           split_size=None):
    """Create *archive_path* from *sources*.

    Parameters
    ----------
    archive_path:
        Output path.  The format is taken from its extension unless *fmt* is
        given.
    sources:
        A path or list of paths (files and/or folders) to archive.
    fmt:
        Explicit format override (see :data:`archivepro.formats.FORMATS`).
    password:
        Only honoured for ``7z`` (AES-256 + encrypted header).  Requesting a
        password for any other format raises :class:`ArchiveError`.
    level:
        Compression level (format-dependent; ``None`` uses a sensible default).
    split_size:
        If a positive integer, the archive is built and then split into
        ``<archive>.001``, ``.002`` ... volumes with a rejoin manifest; the
        single combined file is not left behind.

    Returns a report ``dict`` with ``added`` (file count), ``total_size``
    (uncompressed input bytes), ``compressed_size`` (bytes written),
    ``ratio`` (compressed / total) and, when split, ``parts``.
    """
    archive_path = os.fspath(archive_path)
    fmt = _resolve_format(archive_path, fmt)

    entries = list(_collect(sources))
    total_size = sum(
        os.path.getsize(full) for full, _arc, is_dir in entries if not is_dir
    )

    split_size = int(split_size) if split_size else 0

    if split_size > 0:
        tmp_dir = tempfile.mkdtemp(prefix="archivepro_")
        tmp_archive = os.path.join(tmp_dir, os.path.basename(archive_path))
        try:
            added = _build(tmp_archive, fmt, entries, password, level)
            parts, compressed_size, manifest = write_split(
                tmp_archive, archive_path, fmt, split_size
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        report = {
            "archive": archive_path,
            "format": fmt,
            "added": added,
            "total_size": total_size,
            "compressed_size": compressed_size,
            "ratio": (compressed_size / total_size) if total_size else 0.0,
            "parts": parts,
            "manifest": manifest,
            "split": True,
        }
        return report

    # Build directly; clean up a half-written file on failure.
    try:
        added = _build(archive_path, fmt, entries, password, level)
    except BaseException:
        if os.path.exists(archive_path):
            try:
                os.remove(archive_path)
            except OSError:
                pass
        raise

    compressed_size = os.path.getsize(archive_path)
    return {
        "archive": archive_path,
        "format": fmt,
        "added": added,
        "total_size": total_size,
        "compressed_size": compressed_size,
        "ratio": (compressed_size / total_size) if total_size else 0.0,
        "parts": [],
        "manifest": None,
        "split": False,
    }


__all__ = ["create", "SPLIT_MANIFEST_SUFFIX"]
