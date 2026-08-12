# ArchivePro

A fast, **offline**, **100% open-source** multi-format archiver for Windows. Nothing is uploaded anywhere. Built entirely by AI with human testing and guidance, and published on [QuickOpen](https://quickopen.ai/projects/archive-pro).

> **100% AI-built and open source.** Apache-2.0.

## What it does

Create and extract ZIP, 7z, TAR, GZ and Zstandard archives with a familiar dual-pane browser; add AES-256 encryption and password protection, split into volumes, test integrity, and set compression levels. A capable, fully open-source 7-Zip/WinRAR alternative.

## Install

Download **`ArchivePro-Setup.exe`** from the [QuickOpen page](https://quickopen.ai/projects/archive-pro) or the [GitHub release](https://github.com/quickpod/archive-pro/releases/latest) and double-click it. It installs per-user, adds Desktop and Start Menu shortcuts, and can optionally trust the QuickOpen Root CA. Authenticode-signed by the QuickOpen Code Signing CA — verify at [quickopen.ai/trust](https://quickopen.ai/trust).

## Run from source

```sh
pip install -r requirements.txt
python archive_pro_app.py          # GUI
python -m archivepro --help    # CLI
```


## Features

- **Formats:** ZIP, 7z, TAR, TAR+gzip (`.tar.gz`/`.tgz`), TAR+bzip2 (`.tar.bz2`), TAR+xz (`.tar.xz`), single-file Zstandard (`.zst`) and single-file gzip (`.gz`). The format is inferred from the file name or set explicitly.
- **Encryption:** password-protected **7z** with AES-256 and an encrypted header (names hidden). ZIP/TAR/single-file formats are not encryptable — ArchivePro tells you to use 7z instead of silently writing a plaintext archive.
- **Compression levels:** per-format level control (`--level`), with sensible defaults.
- **Split volumes:** `--split 10M` builds `.001`, `.002` … volumes plus a `.manifest.json`; extraction transparently rejoins and verifies them with a SHA-256 check.
- **Browse & selective extract:** list an archive's entries (name, size, compressed size, modified, dir flag) and extract everything or just chosen members.
- **Integrity testing:** verify an archive (or split set) is complete and uncorrupted.
- **Path-traversal safe:** every member path is sanitised before writing — a crafted `../evil` or absolute-path entry is refused, so nothing is ever written outside the destination folder ("Zip Slip" defence).
- **Overwrite protection:** extraction refuses to clobber existing files unless you opt in.
- **Dual-pane GUI:** a pure-tkinter desktop app (dark/light) with Browse / Create / Test views, threaded operations, recent archives, and inline error reporting — no third-party GUI dependencies.

## CLI examples

```sh
# Create a password-protected 7z (AES-256 + encrypted header)
python -m archivepro create backup.7z ./photos ./notes.txt --password 's3cr3t'

# Create a tar.xz at a chosen compression level
python -m archivepro create site.tar.xz ./public --level 6

# Split a large archive into 100 MB volumes
python -m archivepro create big.7z ./dataset --split 100M

# List an archive's contents
python -m archivepro list backup.7z --password 's3cr3t'

# Test integrity (exit code is non-zero if corrupt)
python -m archivepro test backup.7z --password 's3cr3t'

# Extract everything (or only selected members) to a folder
python -m archivepro extract backup.7z ./restore --password 's3cr3t'
python -m archivepro extract site.tar.xz ./out --members public/index.html
```

## License

Apache-2.0 — see [LICENSE](LICENSE). A 100% AI-built project published on QuickOpen.
