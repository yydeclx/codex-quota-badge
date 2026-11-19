import subprocess
import unittest
from unittest.mock import MagicMock, patch

from sidebar_plugin import SidebarPlugin
from sidebar_context import ContextMenu


class ContextLifetimeTests(unittest.TestCase):
    def test_repeated_opens_share_menu_until_exit(self):
        sidebar = MagicMock()
        sidebar.context_process = None
        sidebar.items = [{'id': 'weekly_quota'}]
        sidebar.scale = 1
        sidebar.hwnd = 1
        sidebar.win = MagicMock()
        sidebar.index_at = MagicMock(return_value=0)
        sidebar.track_hover = MagicMock()
        with patch('subprocess.Popen') as launch, patch('interaction_runtime.trace'):
            launch.return_value.poll.return_value = None
            for _ in range(10):
                SidebarPlugin.context_menu(sidebar, 1, 0)
            launch.assert_called_once()
            launch.return_value.poll.return_value = 0
            SidebarPlugin.context_menu(sidebar, 1, 0)
            self.assertEqual(launch.call_count, 2)

    def test_settings_outlives_menu(self):
        menu = ContextMenu.__new__(ContextMenu)
        menu.root = MagicMock()
        menu.action = 'manager'
        with patch.object(subprocess, 'Popen') as launch:
            menu.run()
        menu.root.destroy.assert_called_once()
        self.assertTrue(launch.call_args.args[0][-1].endswith('sidebar_manager.py'))
