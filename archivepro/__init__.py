"""archivepro -- a permissively-licensed, offline multi-format archiver library.

Public API::

    from archivepro import create, extract, list_contents, test_archive

    create("out.7z", ["docs/"], password="secret")
    for e in list_contents("out.7z", password="secret"):
        print(e["name"], e["size"])
    extract("out.7z", "unpacked/", password="secret")
    assert test_archive("out.7z", password="secret")

Every function raises :class:`ArchiveError` (and only that) on failure, so the
CLI and the tkinter GUI can share one error path.  Supported formats: zip, 7z
(AES-256 + encrypted header), tar, tar.gz/tgz, tar.bz2, tar.xz, and the
single-file zstandard (.zst) and gzip (.gz) streams.
"""

from __future__ import annotations

from .errors import ArchiveError
from .formats import (
    FORMATS,
    SINGLE_FILE_FORMATS,
    detect_format,
    normalize_format,
)
from .create import create
from .extract import extract
from .inspect import list_contents, test_archive

__version__ = "1.1.0"

__all__ = [
    "ArchiveError",
    "FORMATS",
    "SINGLE_FILE_FORMATS",
    "create",
    "extract",
    "list_contents",
    "test_archive",
    "detect_format",
    "normalize_format",
    "__version__",
]
