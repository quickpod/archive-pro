"""Archive format detection and small format helpers.

A *format* here is one of the short identifiers in :data:`FORMATS`.  Detection
prefers the filename extension (the only way to tell ``foo.tar.gz`` from a plain
``foo.gz`` single-file stream) and falls back to magic bytes when the extension
is unknown.  Nothing in this module opens or extracts an archive -- it only
classifies.
"""

from __future__ import annotations

import os

from .errors import ArchiveError

# The formats archivepro can create and read.
FORMATS = ("zip", "7z", "tar", "tar.gz", "tar.bz2", "tar.xz", "zst", "gz")

# Formats that hold exactly one file (no directory tree, no member list).
SINGLE_FILE_FORMATS = ("zst", "gz")

# tarfile open-mode suffix for each tar flavour ("" == uncompressed).
TAR_MODES = {"tar": "", "tar.gz": "gz", "tar.bz2": "bz2", "tar.xz": "xz"}

# Extension -> format.  Longer/compound suffixes are checked first so that
# ".tar.gz" wins over ".gz".
_EXT_TABLE = (
    ((".tar.gz", ".tgz"), "tar.gz"),
    ((".tar.bz2", ".tbz2", ".tbz"), "tar.bz2"),
    ((".tar.xz", ".txz"), "tar.xz"),
    ((".tar",), "tar"),
    ((".zip",), "zip"),
    ((".7z",), "7z"),
    ((".zst",), "zst"),
    ((".gz",), "gz"),
)

# Magic-byte signatures (offset 0 unless noted).
_MAGIC = (
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),   # empty zip
    (b"PK\x07\x08", "zip"),   # spanned zip
    (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"\xfd7zXZ\x00", "tar.xz"),   # xz stream -> assume tar.xz
    (b"BZh", "tar.bz2"),           # bzip2 stream -> assume tar.bz2
    (b"\x28\xb5\x2f\xfd", "zst"),
    (b"\x1f\x8b", "gz"),           # gzip stream (may be tar.gz; see refine)
)


def format_from_extension(path):
    """Return the format implied by *path*'s extension, or ``None``."""
    name = os.path.basename(str(path)).lower()
    for suffixes, fmt in _EXT_TABLE:
        for suffix in suffixes:
            if name.endswith(suffix):
                return fmt
    return None


def _sniff_magic(path):
    """Return the format guessed from the leading magic bytes, or ``None``."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
    except OSError:
        return None
    for sig, fmt in _MAGIC:
        if head.startswith(sig):
            if fmt == "gz":
                # A gzip stream wrapping a tar is by far the common case for
                # multi-file archives; but a bare ".gz" is a single file.  We
                # cannot know from the magic alone, so keep it as single-file
                # here -- callers with an extension use that instead.
                return "gz"
            return fmt
    # tar has its "ustar" marker at offset 257.
    if len(head) >= 262 and head[257:262] == b"ustar":
        return "tar"
    return None


def detect_format(path, sources_hint=None):
    """Detect the archive format of *path*.

    Extension wins; magic bytes are the fallback for an unknown extension on an
    existing file.  Raises :class:`ArchiveError` when the format cannot be
    determined.  *sources_hint* is unused today but reserved for create-time
    disambiguation.
    """
    fmt = format_from_extension(path)
    if fmt:
        return fmt
    if os.path.exists(path):
        fmt = _sniff_magic(path)
        if fmt:
            return fmt
    raise ArchiveError(
        f"cannot determine archive format for {os.path.basename(str(path))!r}; "
        f"use a known extension ({', '.join(FORMATS)}) or pass an explicit format"
    )


def normalize_format(fmt):
    """Validate/normalise a user-supplied format string."""
    if fmt is None:
        return None
    key = str(fmt).strip().lower().lstrip(".")
    aliases = {
        "tgz": "tar.gz", "tbz": "tar.bz2", "tbz2": "tar.bz2", "txz": "tar.xz",
        "gzip": "gz", "zstd": "zst", "bzip2": "tar.bz2", "xz": "tar.xz",
        "targz": "tar.gz",
    }
    key = aliases.get(key, key)
    if key not in FORMATS:
        raise ArchiveError(
            f"unsupported format {fmt!r}; choose one of: {', '.join(FORMATS)}"
        )
    return key


def is_tar_format(fmt):
    return fmt in TAR_MODES


def is_single_file_format(fmt):
    return fmt in SINGLE_FILE_FORMATS


def single_file_output_name(archive_path):
    """The natural output filename for a single-file archive.

    ``data.txt.gz`` -> ``data.txt``; ``blob.zst`` -> ``blob``.
    """
    base = os.path.basename(str(archive_path))
    for suffix in (".gz", ".zst"):
        if base.lower().endswith(suffix):
            return base[: -len(suffix)] or base + ".out"
    return base + ".out"
