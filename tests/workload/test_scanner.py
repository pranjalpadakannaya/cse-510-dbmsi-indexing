import os
import tempfile
import unittest

from dbmsi.workload.scanner import MetadataScanner, scan_directory


def touch(root, rel, size=0, mtime=None):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class TestScanner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        touch(self.root, "b.txt", 10, mtime=1_600_000_000)
        touch(self.root, "a/x.py", 3)
        touch(self.root, "a/deep/er/report.pdf", 2048)
        touch(self.root, ".hidden/secret.md", 1)
        touch(self.root, "c/.dotfile", 5)

    def tearDown(self):
        self._tmp.cleanup()

    def test_scans_all_regular_files_in_sorted_order(self):
        records = scan_directory(self.root)
        self.assertEqual(
            [r.path for r in records],
            ["b.txt", ".hidden/secret.md", "a/x.py", "a/deep/er/report.pdf", "c/.dotfile"],
        )
        self.assertEqual([r.file_id for r in records], list(range(5)))
        by_path = {r.path: r for r in records}
        self.assertEqual(by_path["a/deep/er/report.pdf"].size, 2048)
        self.assertEqual(by_path["a/deep/er/report.pdf"].depth, 3)
        self.assertEqual(by_path["b.txt"].mtime, 1_600_000_000)
        self.assertEqual(by_path["b.txt"].uid, os.getuid())

    def test_is_deterministic(self):
        self.assertEqual(scan_directory(self.root), scan_directory(self.root))

    def test_hidden_and_limit(self):
        paths = [r.path for r in scan_directory(self.root, include_hidden=False)]
        self.assertEqual(paths, ["b.txt", "a/x.py", "a/deep/er/report.pdf"])
        self.assertEqual(len(scan_directory(self.root, max_files=2)), 2)

    def test_symlinks_skipped_by_default_and_cycles_safe(self):
        os.symlink(os.path.join(self.root, "b.txt"), os.path.join(self.root, "link.txt"))
        os.symlink(self.root, os.path.join(self.root, "a", "loop"))
        scanner = MetadataScanner(self.root)
        paths = [r.path for r in scanner.scan()]
        self.assertNotIn("link.txt", paths)
        self.assertEqual(scanner.stats.skipped, 2)

        followed = [r.path for r in scan_directory(self.root, follow_symlinks=True)]
        self.assertIn("link.txt", followed)
        self.assertEqual(len(followed), 6)  # the loop back to root is not re-entered

    def test_unreadable_directory_is_counted_not_fatal(self):
        locked = os.path.join(self.root, "locked")
        touch(self.root, "locked/f.txt")
        os.chmod(locked, 0)
        try:
            if os.access(locked, os.R_OK):
                self.skipTest("running with privileges that bypass permissions")
            scanner = MetadataScanner(self.root)
            self.assertEqual(len(list(scanner.scan())), 5)
            self.assertEqual(scanner.stats.errors, 1)
        finally:
            os.chmod(locked, 0o755)

    def test_rejects_missing_root(self):
        with self.assertRaises(NotADirectoryError):
            MetadataScanner(os.path.join(self.root, "nope"))


if __name__ == "__main__":
    unittest.main()
