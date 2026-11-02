"""Real manager controls and atomic saves, isolated from live configuration."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import ttk
import types
import unittest
from unittest.mock import Mock, patch

from sidebar_data import load_config, validate_config
from sidebar_manager import Manager


def sample_config():
    return {'version':1, 'items':[{'id':'weekly', 'type':'weekly_quota',
                                 'label':'周额度', 'enabled':True}],
            'realtimeMetricsEnabled':True}


class QuotaAccountValidationTests(unittest.TestCase):
    def test_missing_account_defaults_to_main(self):
        self.assertEqual(validate_config(sample_config())['quotaAccount'], 'main')

    def test_both_accounts_are_accepted(self):
        for account in ('main', 'secondary'):
            with self.subTest(account=account):
                config=dict(sample_config(), quotaAccount=account)
                self.assertEqual(validate_config(config)['quotaAccount'], account)

    def test_invalid_values_are_rejected(self):
        for value in ('', 'MAIN', 'Secondary', ' main ', '主账号', 'other',
                      None, True, False, 0, 1, [], {}, ['main']):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '额度显示账号'):
                validate_config(dict(sample_config(), quotaAccount=value))


class QuotaAccountManagerTests(unittest.TestCase):
    def setUp(self):
        directory=self.enterContext(tempfile.TemporaryDirectory())
        self.path=Path(directory)/'sidebar-config.json'
        self.enterContext(patch('sidebar_data.CONFIG', self.path))
        self.path.write_text(json.dumps(sample_config(), ensure_ascii=False), encoding='utf-8')
        self.enterContext(patch('sidebar_manager.sidebar_startup.enabled', return_value=False))
        self.startup=self.enterContext(patch('sidebar_manager.sidebar_startup.set_enabled'))
        self.notify=self.enterContext(patch('sidebar_manager.notify_sidebar', return_value=True))
        self.error=self.enterContext(patch('sidebar_manager.messagebox.showerror'))
        self.mode=Mock()
        self.enterContext(patch.dict(sys.modules, patch_mode=types.SimpleNamespace(apply_mode=self.mode)))
        self.enterContext(patch('sidebar_manager.subprocess.Popen', side_effect=AssertionError('Unexpected launch')))
        self.enterContext(patch('manager_icons.prepare_identity'))
        self.enterContext(patch('manager_icons.set_window_icons'))
        self.enterContext(patch('ui_presence.mark'))
        # Build real Tk widgets without showing a test window or changing focus.
        self.enterContext(patch.object(tk.Tk, 'deiconify'))
        self.enterContext(patch.object(Manager, 'reveal'))

    def manager(self):
        manager=Manager(initial_page=2)
        self.addCleanup(manager.root.destroy)
        manager.root.update_idletasks()
        return manager

    def widgets(self, parent):
        for widget in parent.winfo_children():
            yield widget
            yield from self.widgets(widget)

    def choose(self, manager, title):
        controls=[widget for widget in self.widgets(manager.pages[2])
                  if isinstance(widget, ttk.Radiobutton) and widget.cget('text')==title]
        self.assertEqual(len(controls), 1)
        controls[0].invoke()

    def test_legacy_config_initializes_main_without_dirty_or_write(self):
        before=self.path.read_bytes()
        manager=self.manager()
        self.assertEqual(manager.quota_account.get(), 'main')
        self.assertFalse(manager.dirty)
        self.assertEqual(manager.edit_revision, 0)
        self.assertEqual(self.path.read_bytes(), before)
        labels=[str(widget.cget('text')) for widget in self.widgets(manager.pages[2])
                if 'text' in widget.keys()]
        for text in ('额度显示账号', '主账号', '副账号', '只切换额度展示', '不切换请求账号'):
            self.assertIn(text, labels)

    def test_legacy_save_persists_main(self):
        manager=self.manager()
        self.assertTrue(manager.save())
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['quotaAccount'], 'main')
        self.assertFalse(manager.dirty)
        self.mode.assert_not_called()

    def test_control_roundtrip_preserves_other_settings_and_restores_selection(self):
        manager=self.manager()
        baseline=load_config()
        for title, account in (('副账号', 'secondary'), ('主账号', 'main'), ('副账号', 'secondary')):
            with self.subTest(account=account):
                before=self.path.read_bytes()
                revision=manager.edit_revision
                self.choose(manager, title)
                self.assertEqual(manager.quota_account.get(), account)
                self.assertTrue(manager.dirty)
                self.assertEqual(manager.edit_revision, revision+1)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertTrue(manager.save())
                self.assertFalse(manager.dirty)
                self.assertEqual(load_config(), dict(baseline, quotaAccount=account))
                self.assertEqual(manager.config['quotaAccount'], account)
        reopened=self.manager()
        self.assertEqual(reopened.quota_account.get(), 'secondary')
        self.assertFalse(reopened.dirty)
        self.assertEqual(self.notify.call_count, 3)
        self.mode.assert_not_called()
        self.error.assert_not_called()

    def test_unrelated_setting_save_preserves_selected_account(self):
        self.path.write_text(json.dumps(dict(sample_config(), quotaAccount='secondary')), encoding='utf-8')
        manager=self.manager()
        manager.refresh.set(120)
        self.assertTrue(manager.save())
        self.assertEqual(load_config()['quotaAccount'], 'secondary')
        self.assertEqual(load_config()['refreshSeconds'], 120)

    def test_invalid_selection_rejects_save_before_external_actions(self):
        manager=self.manager()
        before=self.path.read_bytes()
        manager.quota_account.set('invalid')
        self.assertFalse(manager.save())
        self.assertTrue(manager.dirty)
        self.assertEqual(self.path.read_bytes(), before)
        self.error.assert_called_once()
        self.startup.assert_not_called()
        self.notify.assert_not_called()
        self.mode.assert_not_called()

    def test_atomic_save_failure_keeps_previous_account_and_dirty_edit(self):
        manager=self.manager()
        before=self.path.read_bytes()
        self.choose(manager, '副账号')
        with patch.object(Path, 'replace', side_effect=OSError('atomic write failed')):
            self.assertFalse(manager.save())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(load_config()['quotaAccount'], 'main')
        self.assertEqual(manager.quota_account.get(), 'secondary')
        self.assertEqual(manager.config['quotaAccount'], 'main')
        self.assertTrue(manager.dirty)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])
        self.notify.assert_not_called()
        self.error.assert_called_once()

    def test_async_save_captures_selection_and_keeps_later_edit_dirty(self):
        manager=self.manager()
        self.choose(manager, '副账号')
        manager.realtime_metrics_enabled.set(False)
        entered=threading.Event();release=threading.Event()

        def apply_mode(enabled):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Test worker timed out')

        self.mode.side_effect=apply_mode
        try:
            self.assertFalse(manager.save())
            self.assertTrue(entered.wait(2))
            self.assertTrue(manager.switching)
            self.choose(manager, '主账号')
        finally:
            release.set()
        result=manager.save_results.get(timeout=3)
        self.assertIsNone(result[2])
        manager.save_results.put(result)
        manager.poll_save_result()
        self.assertEqual(load_config()['quotaAccount'], 'secondary')
        self.assertEqual(manager.quota_account.get(), 'main')
        self.assertTrue(manager.dirty)
        self.assertFalse(manager.switching)
        self.assertIn('仍有未保存', manager.message.get())
        # The next normal save commits the newer UI edit through the same path.
        self.assertTrue(manager.save())
        self.assertEqual(load_config()['quotaAccount'], 'main')
        self.assertFalse(manager.dirty)
        self.mode.assert_called_once_with(False)
        self.error.assert_not_called()


if __name__=='__main__':
    unittest.main()
