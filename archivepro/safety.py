"""Path-traversal defence shared by the extractor.

The single rule: a member of an archive may only ever be written *inside* the
chosen destination directory.  Absolute paths, drive letters, and ``..``
components that would climb out are rejected -- this is the classic "Zip Slip"
class of vulnerability and every extraction path in this package goes through
:func:`safe_join`.
"""

from __future__ import annotations

import os

from .errors import ArchiveError


def _is_unsafe_name(name):
    if not name:
        return True
    # Normalise separators; reject absolute paths and Windows drive/UNC forms.
    norm = name.replace("\\", "/")
    if norm.startswith("/"):
        return True
    if os.path.isabs(name):
        return True
    if len(norm) >= 2 and norm[1] == ":":  # e.g. "C:..."
        return True
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return True
    return False


def safe_join(dest_dir, member_name):
    """Return the absolute, validated path for *member_name* under *dest_dir*.

    Raises :class:`ArchiveError` if the member would escape *dest_dir*.
    """
    dest_dir = os.path.abspath(dest_dir)
    if _is_unsafe_name(member_name):
        raise ArchiveError(
            f"refusing to extract unsafe path {member_name!r} (it would write "
            f"outside the destination folder)"
        )
    target = os.path.abspath(os.path.join(dest_dir, member_name))
    # Belt-and-braces: confirm the resolved path really is contained.
    if target != dest_dir and not target.startswith(dest_dir + os.sep):
        raise ArchiveError(
            f"refusing to extract unsafe path {member_name!r} (it would write "
            f"outside the destination folder)"
        )
    return target


def assert_names_safe(dest_dir, names):
    """Validate a whole batch of member names up front (fail fast)."""
    for name in names:
        safe_join(dest_dir, name)
