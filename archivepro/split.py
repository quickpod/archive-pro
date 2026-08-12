"""Split a finished archive into fixed-size volumes and rejoin them.

A split produces ``<archive>.001``, ``<archive>.002`` ... plus a small JSON
manifest (``<archive>.manifest.json``) recording the part order, the original
size and a SHA-256 of the whole archive so a rejoin can verify it reconstructed
the original bytes exactly.  This raw-split scheme works uniformly for every
format, including 7z.
"""

from __future__ import annotations

import hashlib
import json
import os

from .errors import ArchiveError

SPLIT_MANIFEST_SUFFIX = ".manifest.json"
_CHUNK = 1024 * 1024


def part_path(base_archive_path, index):
    """Return the volume path for 1-based *index* (``.001`` ...)."""
    return f"{base_archive_path}.{index:03d}"


def manifest_path(base_archive_path):
    return base_archive_path + SPLIT_MANIFEST_SUFFIX


def write_split(source_archive, base_archive_path, fmt, split_size):
    """Split *source_archive* into volumes next to *base_archive_path*.

    Returns ``(part_paths, total_compressed_size, manifest_path)``.
    """
    if split_size <= 0:
        raise ArchiveError("split size must be a positive number of bytes")

    parent = os.path.dirname(os.path.abspath(base_archive_path))
    os.makedirs(parent, exist_ok=True)

    sha = hashlib.sha256()
    total = 0
    parts = []
    index = 1
    with open(source_archive, "rb") as fin:
        while True:
            written = 0
            out = part_path(base_archive_path, index)
            with open(out, "wb") as fout:
                while written < split_size:
                    chunk = fin.read(min(_CHUNK, split_size - written))
                    if not chunk:
                        break
                    fout.write(chunk)
                    sha.update(chunk)
                    written += len(chunk)
                    total += len(chunk)
            if written == 0:
                # Nothing went into this volume: it is spurious, drop it.
                os.remove(out)
                break
            parts.append(out)
            index += 1
            if written < split_size:
                break

    if not parts:  # empty archive edge case -> one empty volume
        out = part_path(base_archive_path, 1)
        with open(out, "wb"):
            pass
        parts = [out]

    manifest = {
        "archive": os.path.basename(base_archive_path),
        "format": fmt,
        "size": total,
        "sha256": sha.hexdigest(),
        "part_size": split_size,
        "parts": [os.path.basename(p) for p in parts],
    }
    mpath = manifest_path(base_archive_path)
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return parts, total, mpath


def is_split(base_archive_path):
    """True when *base_archive_path* looks like a split set (manifest present)."""
    return os.path.exists(manifest_path(base_archive_path))


def load_manifest(base_archive_path):
    mpath = manifest_path(base_archive_path)
    try:
        with open(mpath, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ArchiveError(f"cannot read split manifest {mpath}: {exc}") from exc
    if not isinstance(data, dict) or "parts" not in data:
        raise ArchiveError(f"malformed split manifest: {mpath}")
    return data


def rejoin(base_archive_path, dest_path):
    """Reconstruct the original archive from its volumes into *dest_path*.

    Verifies the recombined bytes against the manifest SHA-256.
    """
    manifest = load_manifest(base_archive_path)
    parent = os.path.dirname(os.path.abspath(base_archive_path))
    sha = hashlib.sha256()
    with open(dest_path, "wb") as fout:
        for name in manifest["parts"]:
            part = os.path.join(parent, os.path.basename(name))
            if not os.path.exists(part):
                raise ArchiveError(f"missing split volume: {name}")
            with open(part, "rb") as fin:
                while True:
                    chunk = fin.read(_CHUNK)
                    if not chunk:
                        break
                    fout.write(chunk)
                    sha.update(chunk)
    expected = manifest.get("sha256")
    if expected and sha.hexdigest() != expected:
        raise ArchiveError(
            "split volumes failed integrity check (a volume is corrupt or "
            "missing); the archive could not be rejoined"
        )
    return manifest.get("format")
