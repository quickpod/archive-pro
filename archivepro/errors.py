"""Error type for archivepro."""


class ArchiveError(Exception):
    """Raised for any recoverable failure in an archivepro operation.

    All public functions raise this (and only this) on failure so callers
    -- including the CLI and the GUI -- have a single exception to catch.
    A wrong password, a corrupt/truncated archive, an unsafe member path or
    an unsupported format are all reported as an :class:`ArchiveError`.
    """
