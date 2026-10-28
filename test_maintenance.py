import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import sidebar_maintenance as maintenance

from sidebar_maintenance import clean_known_artifacts, maintain, prune_update_backups, rotate_log_tail, trim_connection_history


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_log_rotation_keeps_tail(self):
        path = self.root / "interaction.log"
        path.write_bytes(b"old-record\n" + b"x" * (1024 * 1024 + 100) + b"\nnew-record\n")
        self.assertTrue(rotate_log_tail(path, 1024 * 1024))
        data = path.read_bytes()
        self.assertLessEqual(len(data), 2 * 1024 * 1024)
        self.assertTrue(data.endswith(b"new-record\n"))
        self.assertNotIn(b"old-record\n", data)

    def test_small_log_is_untouched(self):
        path = self.root / "native-error.log"
        original = b"one\ntwo\n"
        path.write_bytes(original)
        self.assertFalse(rotate_log_tail(path, 1024 * 1024))
        self.assertEqual(path.read_bytes(), original)

    def test_connection_history_keeps_newest_twenty(self):
        path = self.root / "connection-history.json"
        path.write_text(json.dumps(list(range(25))), encoding="utf-8")
        self.assertTrue(trim_connection_history(path))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), list(range(5, 25)))
        self.assertFalse(trim_connection_history(path))

    def test_backup_pruning_keeps_three_newest(self):
        backup = self.root / ".update-backups"
        backup.mkdir()
        for index in range(5):
            item = backup / f"20261028-220000{index:03d}"
            item.mkdir()
            (item / "old.py").write_text(str(index), encoding="utf-8")
            os.utime(item, (time.time() - index, time.time() - index))
        removed = prune_update_backups(self.root)
        self.assertEqual(len(removed), 2)
        self.assertEqual(len(list(backup.iterdir())), 3)

    def test_cleanup_is_allowlisted(self):
        (self.root / "__pycache__").mkdir()
        (self.root / "__pycache__" / "x.pyc").write_bytes(b"x")
        (self.root / "details-preview-test.png").write_bytes(b"x")
        (self.root / "important.png").write_bytes(b"keep")
        (self.root / "sidebar-config.json").write_text("{}", encoding="utf-8")
        removed = clean_known_artifacts(self.root)
        self.assertFalse((self.root / "__pycache__").exists())
        self.assertFalse((self.root / "details-preview-test.png").exists())
        self.assertTrue((self.root / "important.png").exists())
        self.assertTrue((self.root / "sidebar-config.json").exists())
        self.assertEqual(len(removed["pycache"]), 1)

    def test_generic_preview_and_test_names_are_preserved(self):
        protected = ('test-photo.png', 'draft-preview.png', 'preview-business.png')
        for name in protected:
            (self.root / name).write_bytes(b'user data')
        generated = self.root / 'preview-quota-tooltip.png'
        generated.write_bytes(b'generated')
        clean_known_artifacts(self.root)
        self.assertFalse(generated.exists())
        for name in protected:
            self.assertEqual((self.root / name).read_bytes(), b'user data')

    def test_maintain_preserves_protected_files_and_is_repeatable(self):
        (self.root / "interaction.log").write_text("ok\n", encoding="utf-8")
        (self.root / "connection-history.json").write_text("[]", encoding="utf-8")
        protected = {"sidebar-config.json": "{}", "quota-history.json": "[]", "status.json": "{}", "credentials.json": "secret"}
        for name, value in protected.items():
            (self.root / name).write_text(value, encoding="utf-8")
        first = maintain(self.root)
        second = maintain(self.root)
        self.assertEqual(second["rotated_logs"], [])
        for name, value in protected.items():
            self.assertEqual((self.root / name).read_text(encoding="utf-8"), value)
        self.assertIn("connection_history_trimmed", first)

    def test_cleanup_never_recurses_into_dependencies_or_backups(self):
        for folder in ('backups', '.update-backups', 'gateway-telemetry', 'package'):
            cache = self.root / folder / '__pycache__'
            cache.mkdir(parents=True)
            (cache / 'keep.pyc').write_bytes(b'keep')
        with patch.object(Path, 'rglob', side_effect=AssertionError('recursive scan')):
            self.assertEqual(clean_known_artifacts(self.root)['pycache'], [])
        for folder in ('backups', '.update-backups', 'gateway-telemetry', 'package'):
            self.assertTrue((self.root / folder / '__pycache__' / 'keep.pyc').exists())

    def test_backup_pruning_preserves_unknown_names_and_continues_after_failure(self):
        backup = self.root / '.update-backups'
        backup.mkdir()
        for index in range(5):
            item = backup / f'20261028-220000{index:03d}'
            item.mkdir()
            os.utime(item, (100 - index, 100 - index))
        unknown = backup / 'manual-save'
        unknown.mkdir()
        original = maintenance.shutil.rmtree
        def remove(path):
            if path.name.endswith('003'):
                raise PermissionError('locked')
            return original(path)
        with patch.object(maintenance.shutil, 'rmtree', side_effect=remove):
            self.assertEqual(prune_update_backups(self.root), ['20261028-220000004'])
        self.assertTrue(unknown.is_dir())
        self.assertTrue((backup / '20261028-220000003').is_dir())

    def test_history_changed_during_read_is_preserved(self):
        path = self.root / 'connection-history.json'
        path.write_text(json.dumps(list(range(25))), encoding='utf-8')
        original = maintenance._atomic_json_write
        def concurrent_write(target, value, signature):
            target.write_text('["new-record"]', encoding='utf-8')
            return original(target, value, signature)
        with patch.object(maintenance, '_atomic_json_write', side_effect=concurrent_write):
            self.assertFalse(trim_connection_history(path))
        self.assertEqual(json.loads(path.read_text()), ['new-record'])
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_log_changed_during_rotation_is_preserved(self):
        path = self.root / 'interaction.log'
        path.write_bytes(b'x' * (2 * 1024 * 1024 + 1))
        original = maintenance.os.fsync
        def concurrent_write(fd):
            original(fd)
            with path.open('ab') as stream:
                stream.write(b'\nnew-record\n')
        with patch.object(maintenance.os, 'fsync', side_effect=concurrent_write):
            self.assertFalse(rotate_log_tail(path))
        self.assertTrue(path.read_bytes().endswith(b'new-record\n'))
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_reparse_attribute_is_rejected(self):
        path = self.root / 'interaction.log'
        path.write_bytes(b'keep')
        original = Path.lstat
        def fake_lstat(item):
            if item == path:
                from types import SimpleNamespace
                return SimpleNamespace(st_file_attributes=0x400, st_mode=0)
            return original(item)
        with patch.object(Path, 'lstat', fake_lstat):
            self.assertFalse(rotate_log_tail(path))
        self.assertEqual(path.read_bytes(), b'keep')

    def test_linked_maintenance_root_is_not_followed(self):
        actual = self.root / 'actual'
        actual.mkdir()
        (actual / 'test-keep.png').write_bytes(b'keep')
        link = self.root / 'link'
        try:
            link.symlink_to(actual, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f'symlinks unavailable: {exc}')
        self.assertEqual(clean_known_artifacts(link), {'pycache': [], 'previews': []})
        self.assertTrue((actual / 'test-keep.png').exists())

    def test_failed_log_rotation_does_not_block_other_maintenance(self):
        path = self.root / 'interaction.log'
        path.write_bytes(b'x' * (2 * 1024 * 1024 + 1))
        history = self.root / 'connection-history.json'
        history.write_text(json.dumps(list(range(25))), encoding='utf-8')
        replace = maintenance.os.replace
        def fail_one(source, target):
            if target == path:
                raise PermissionError('locked')
            return replace(source, target)
        with patch.object(maintenance.os, 'replace', side_effect=fail_one):
            result = maintain(self.root)
        self.assertEqual(result['rotated_logs'], [])
        self.assertTrue(result['connection_history_trimmed'])
        self.assertEqual(len(list(self.root.iterdir())), 2)


if __name__ == "__main__":
    unittest.main()
