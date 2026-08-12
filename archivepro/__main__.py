"""Command-line interface: ``python -m archivepro <command> ...``.

Commands: ``create``, ``extract``, ``list``, ``test``.  Any operation failure
is reported as a one-line ``error: ...`` on stderr with a non-zero exit code --
never a traceback.
"""

from __future__ import annotations

import argparse
import sys

from .errors import ArchiveError
from .formats import FORMATS
from .create import create
from .extract import extract
from .inspect import list_contents, test_archive


def _human_size(num_bytes):
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"  # pragma: no cover


def _parse_size(text):
    """Parse a size like ``10M`` / ``1.5g`` / ``2048`` into bytes."""
    if text is None:
        return None
    s = str(text).strip().lower()
    if not s:
        return None
    units = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}
    mult = 1
    if s[-1] in units:
        mult = units[s[-1]]
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError as exc:
        raise ArchiveError(f"invalid size value: {text!r}") from exc


# --- command handlers -------------------------------------------------------


def cmd_create(a):
    report = create(
        a.archive,
        a.sources,
        fmt=a.format,
        password=a.password,
        level=a.level,
        split_size=_parse_size(a.split),
    )
    ratio = report["ratio"] * 100 if report["total_size"] else 0.0
    print(
        f"Created {report['format']} archive with {report['added']} file(s): "
        f"{_human_size(report['total_size'])} -> "
        f"{_human_size(report['compressed_size'])} ({ratio:.0f}% of original)"
    )
    if report.get("split"):
        print(f"Split into {len(report['parts'])} volume(s); "
              f"manifest: {report['manifest']}")


def cmd_extract(a):
    dest = a.dest or "."
    n = extract(
        a.archive,
        dest,
        password=a.password,
        members=a.members,
        overwrite=a.overwrite,
    )
    print(f"Extracted {n} file(s) to {dest}")


def cmd_list(a):
    entries = list_contents(a.archive, password=a.password)
    print(f"{'Size':>12}  {'Compressed':>12}  Name")
    total = 0
    for e in entries:
        total += e["size"]
        comp = "-" if e["compressed"] is None else _human_size(e["compressed"])
        tag = "/" if e["is_dir"] else ""
        print(f"{_human_size(e['size']):>12}  {comp:>12}  {e['name']}{tag}")
    print(f"{len(entries)} entr(y/ies), {_human_size(total)} uncompressed")


def cmd_test(a):
    ok = test_archive(a.archive, password=a.password)
    if ok:
        print(f"OK: {a.archive} passed the integrity check")
        return 0
    print(f"FAILED: {a.archive} is corrupt or incomplete", file=sys.stderr)
    return 2


# --- parser -----------------------------------------------------------------


def build_parser():
    p = argparse.ArgumentParser(
        prog="archivepro",
        description="Offline multi-format archiver (zip, 7z, tar.*, zst, gz).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, help, handler):
        sp = sub.add_parser(name, help=help)
        sp.set_defaults(func=handler)
        return sp

    s = add("create", "Create an archive from files/folders", cmd_create)
    s.add_argument("archive")
    s.add_argument("sources", nargs="+")
    s.add_argument("-f", "--format", choices=FORMATS,
                   help="override the format inferred from the extension")
    s.add_argument("-p", "--password", help="7z only: AES-256 + encrypted header")
    s.add_argument("-l", "--level", type=int, help="compression level")
    s.add_argument("--split", help="split into volumes, e.g. 10M / 1.5G")

    s = add("extract", "Extract an archive", cmd_extract)
    s.add_argument("archive")
    s.add_argument("dest", nargs="?", default=".")
    s.add_argument("-p", "--password")
    s.add_argument("-m", "--members", nargs="+",
                   help="extract only these member names")
    s.add_argument("--overwrite", action="store_true",
                   help="replace existing files at the destination")

    s = add("list", "List the contents of an archive", cmd_list)
    s.add_argument("archive")
    s.add_argument("-p", "--password")

    s = add("test", "Verify archive integrity", cmd_test)
    s.add_argument("archive")
    s.add_argument("-p", "--password")

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        rc = args.func(args)
    except ArchiveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        print("interrupted", file=sys.stderr)
        return 130
    return 0 if rc is None else rc


if __name__ == "__main__":
    sys.exit(main())
