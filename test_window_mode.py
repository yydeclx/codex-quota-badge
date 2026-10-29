import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sidebar_supervisor
import window_mode as mode


class WindowModeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for name in mode.ENTRIES.values():(self.root/name).write_text('',encoding='utf-8')

    def test_default_is_child_even_when_compat_file_exists(self):
        self.assertEqual(mode.load_mode(self.root),'child')
        self.assertEqual(mode.child_entry(self.root).name,'sidebar_plugin.py')
        with patch.object(sidebar_supervisor,'ROOT',self.root):
            self.assertEqual(Path(sidebar_supervisor.child_command()[1]).name,'sidebar_plugin.py')

    def test_explicit_mode_survives_manager_config_replacement(self):
        file=self.root/mode.MODE_FILE;file.write_bytes(b'{"mode":"owned-popup"}\n')
        before=file.read_bytes();(self.root/'sidebar-config.json').write_text('{}')
        self.assertEqual(mode.migrate(self.root),{'created':False,'mode':'owned-popup'})
        self.assertEqual(file.read_bytes(),before)
        with patch.object(sidebar_supervisor,'ROOT',self.root):
            self.assertEqual(Path(sidebar_supervisor.child_command()[1]).name,'sidebar_compat.py')

    def test_legacy_child_pointer_migrates_without_executing_source(self):
        for expression in ("ROOT / 'sidebar_compat.py'", "ROOT.joinpath('sidebar_compat.py')",
                           "Path(__file__).with_name('sidebar_compat.py')", "r'D:\\Tools\\sidebar_compat.py'"):
            with self.subTest(expression=expression):
                file=self.root/mode.MODE_FILE;file.unlink(missing_ok=True)
                (self.root/'sidebar_supervisor.py').write_text(
                    "raise RuntimeError('must never run')\nCHILD="+expression+'\n',encoding='utf-8')
                self.assertEqual(mode.migrate(self.root),{'created':True,'mode':'owned-popup'})
                self.assertEqual(mode.load_mode(self.root),'owned-popup')

    def test_explicit_child_overrides_legacy_and_is_byte_preserved(self):
        (self.root/'sidebar_supervisor.py').write_text("CHILD=ROOT/'sidebar_compat.py'")
        file=self.root/mode.MODE_FILE;file.write_bytes(b'{ "mode" : "child" }\n');before=file.read_bytes()
        self.assertEqual(mode.migrate(self.root),{'created':False,'mode':'child'})
        self.assertEqual(file.read_bytes(),before)

    def test_normal_install_creates_no_machine_file(self):
        (self.root/'sidebar_supervisor.py').write_text("CHILD=ROOT/'sidebar_plugin.py'\n# sidebar_compat.py")
        self.assertEqual(mode.migrate(self.root),{'created':False,'mode':'child'})
        self.assertFalse((self.root/mode.MODE_FILE).exists())

    def test_invalid_choice_or_missing_popup_entry_fails_instead_of_changing_mode(self):
        file=self.root/mode.MODE_FILE;file.write_text('{"mode":"invalid"}')
        with self.assertRaises(ValueError):mode.migrate(self.root)
        file.write_text('{"mode":"owned-popup"}');(self.root/'sidebar_compat.py').unlink()
        with self.assertRaises(FileNotFoundError):mode.child_entry(self.root)


if __name__=='__main__':unittest.main()
