"""Extract archives of any supported format, safely.

Every member path is validated with :func:`archivepro.safety.safe_join` before
a single byte is written, so a crafted ``../evil`` entry can never escape the
destination folder.  Split archives are transparently rejoined first.
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
    detect_format,
    is_single_file_format,
    is_tar_format,
    single_file_output_name,
)
from .safety import assert_names_safe, safe_join
from . import split as _split


def _ensure_parent(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _guard_overwrite(path, overwrite):
    if not overwrite and os.path.exists(path) and not os.path.isdir(path):
        raise ArchiveError(
            f"refusing to overwrite existing file: {path} "
            f"(pass overwrite=True to replace it)"
        )


def _extract_zip(archive_path, dest, password, members, overwrite):
    pwd = password.encode("utf-8") if password else None
    count = 0
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            infos = zf.infolist()
            names = [i.filename for i in infos]
            wanted = set(members) if members else None
            assert_names_safe(dest, [n for n in names if wanted is None or n in wanted])
            for info in infos:
                name = info.filename
                if wanted is not None and name not in wanted:
                    continue
                target = safe_join(dest, name)
                if name.endswith("/") or info.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                _ensure_parent(target)
                _guard_overwrite(target, overwrite)
                with zf.open(info, "r", pwd=pwd) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                count += 1
    except ArchiveError:
        raise
    except (RuntimeError, zipfile.BadZipFile) as exc:
        raise ArchiveError(f"failed to extract zip archive: {exc}") from exc
    return count


def _extract_7z(archive_path, dest, password, members, overwrite):
    import py7zr

    try:
        with py7zr.SevenZipFile(archive_path, "r", password=password) as zf:
            names = zf.getnames()
    except Exception as exc:
        raise ArchiveError(
            f"failed to open 7z archive (wrong password or corrupt file): {exc}"
        ) from exc

    wanted = list(members) if members else None
    check = [n for n in names if wanted is None or n in (wanted or [])]
    assert_names_safe(dest, check)

    if not overwrite:
        for n in check:
            _guard_overwrite(os.path.join(dest, n), overwrite)

    os.makedirs(dest, exist_ok=True)
    try:
        with py7zr.SevenZipFile(archive_path, "r", password=password) as zf:
            if wanted is not None:
                zf.extract(path=dest, targets=wanted)
            else:
                zf.extractall(path=dest)
    except Exception as exc:
        raise ArchiveError(
            f"failed to extract 7z archive (wrong password or corrupt file): "
            f"{exc}"
        ) from exc

    count = 0
    for n in check:
        p = os.path.join(dest, n)
        if os.path.isfile(p):
            count += 1
    return count


def _extract_tar(archive_path, fmt, dest, members, overwrite):
    from .formats import TAR_MODES

    mode = TAR_MODES[fmt]
    wanted = set(members) if members else None
    count = 0
    try:
        with tarfile.open(archive_path, f"r:{mode}" if mode else "r:") as tf:
            selected = []
            for member in tf.getmembers():
                if wanted is not None and member.name not in wanted:
                    continue
                selected.append(member)
            assert_names_safe(dest, [m.name for m in selected])
            for member in selected:
                target = safe_join(dest, member.name)
                if member.isdir():
                    os.makedirs(target, exist_ok=True)
                    continue
                if member.issym() or member.islnk():
                    # Validate the link target stays inside dest, then skip the
                    # actual link creation (data-only extraction is safest).
                    safe_join(dest, os.path.join(os.path.dirname(member.name),
                                                 member.linkname))
                    continue
                if not member.isfile():
                    continue  # skip devices/fifos
                _ensure_parent(target)
                _guard_overwrite(target, overwrite)
                src = tf.extractfile(member)
                if src is None:
                    continue
                with src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                count += 1
    except ArchiveError:
        raise
    except tarfile.TarError as exc:
        raise ArchiveError(f"failed to extract tar archive: {exc}") from exc
    return count


def _extract_single_file(archive_path, fmt, dest, overwrite):
    out_name = single_file_output_name(archive_path)
    target = safe_join(dest, out_name)
    _ensure_parent(target)
    _guard_overwrite(target, overwrite)
    try:
        if fmt == "gz":
            with gzip.open(archive_path, "rb") as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
        else:  # zst
            import zstandard as zstd

            dctx = zstd.ZstdDecompressor()
            with open(archive_path, "rb") as src, open(target, "wb") as out:
                dctx.copy_stream(src, out)
    except ArchiveError:
        raise
    except Exception as exc:
        raise ArchiveError(f"failed to extract {fmt} archive: {exc}") from exc
    return 1


def extract(archive_path, dest, password=None, members=None, overwrite=False):
    """Extract *archive_path* into directory *dest*.

    Parameters
    ----------
    archive_path:
        The archive (or the base name of a split set) to extract.
    dest:
        Destination directory (created if needed).
    password:
        Required for encrypted 7z archives; a wrong password raises
        :class:`ArchiveError`.
    members:
        Optional list of member names to extract (multi-file formats only).
    overwrite:
        When ``False`` (default), extraction refuses to clobber an existing
        file and raises :class:`ArchiveError`.

    Returns the number of files written.  All member paths are sanitised;
    unsafe entries raise :class:`ArchiveError` and nothing escapes *dest*.
    """
    archive_path = os.fspath(archive_path)
    dest = os.fspath(dest)
    os.makedirs(dest, exist_ok=True)

    # Split archive: rejoin volumes into a temp file, then extract that.
    if not os.path.exists(archive_path) and _split.is_split(archive_path):
        tmp_dir = tempfile.mkdtemp(prefix="archivepro_join_")
        joined = os.path.join(tmp_dir, os.path.basename(archive_path))
        try:
            fmt = _split.rejoin(archive_path, joined)
            fmt = fmt or detect_format(joined)
            return _dispatch_extract(joined, fmt, dest, password, members,
                                     overwrite)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if not os.path.exists(archive_path):
        raise ArchiveError(f"archive not found: {archive_path}")

    fmt = detect_format(archive_path)
    return _dispatch_extract(archive_path, fmt, dest, password, members,
                             overwrite)


def _dispatch_extract(archive_path, fmt, dest, password, members, overwrite):
    if fmt == "zip":
        return _extract_zip(archive_path, dest, password, members, overwrite)
    if fmt == "7z":
        return _extract_7z(archive_path, dest, password, members, overwrite)
    if is_tar_format(fmt):
        return _extract_tar(archive_path, fmt, dest, members, overwrite)
    if is_single_file_format(fmt):
        return _extract_single_file(archive_path, fmt, dest, overwrite)
    raise ArchiveError(f"unsupported format: {fmt}")  # pragma: no cover


__all__ = ["extract"]
