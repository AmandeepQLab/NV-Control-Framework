"""Tests for framework.paths.ensure_writable_directory."""

import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from framework.paths import ensure_writable_directory


class EnsureWritableDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)

    def test_creates_missing_directory(self):
        target = self.directory / "new" / "nested"
        self.assertFalse(target.exists())

        ensure_writable_directory(target)

        self.assertTrue(target.is_dir())

    def test_accepts_existing_writable_directory(self):
        ensure_writable_directory(self.directory)  # must not raise

    def test_rejects_a_path_that_is_a_file(self):
        target = self.directory / "not_a_directory.txt"
        target.write_text("x")

        with self.assertRaises(NotADirectoryError):
            ensure_writable_directory(target)

    @unittest.skipUnless(
        os.name == "posix",
        "chmod doesn't restrict writes into a directory on Windows the way "
        "it does on POSIX -- the read-only attribute it toggles there isn't "
        "the same thing as a write-denying ACL.",
    )
    def test_rejects_unwritable_directory(self):
        target = self.directory / "readonly"
        target.mkdir()
        target.chmod(stat.S_IREAD | stat.S_IEXEC)
        self.addCleanup(target.chmod, stat.S_IRWXU)

        with self.assertRaises(OSError):
            ensure_writable_directory(target)


if __name__ == "__main__":
    unittest.main()
