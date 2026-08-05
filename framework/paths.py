"""Filesystem destination checks, kept Qt-free so they're unit-testable
without a QApplication."""

import tempfile
from pathlib import Path


def ensure_writable_directory(path):
    """Create *path* if needed and confirm it's actually writable.

    Raises NotADirectoryError if *path* exists but isn't a directory, or
    OSError (e.g. PermissionError) if it can't be created or written to.
    ``os.access()`` is deliberately not used here -- it's known to be
    unreliable against Windows ACLs -- instead this performs a real
    temp-file write/delete, exercising the actual write path a caller would
    use.
    """
    path = Path(path)
    # Checked before mkdir(): Path.mkdir(exist_ok=True) still raises
    # FileExistsError (not swallowed by exist_ok) when the path exists and
    # is a file rather than a directory, so this can't be a post-mkdir check.
    if path.exists() and not path.is_dir():
        raise NotADirectoryError(f"{path} exists and is not a directory.")
    path.mkdir(parents=True, exist_ok=True)

    probe = tempfile.NamedTemporaryFile(dir=path, delete=True)
    probe.close()
