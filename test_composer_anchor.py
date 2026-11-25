import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, nullcontext
import ctypes as C
from dataclasses import replace
import sys
import unittest
from unittest.mock import patch

import composer_anchor as anchor


def node(kind, rect, name="", classes="", automation_id="", role="", modal=False):
    return anchor._Node(kind, name, automation_id, classes, role, rect, modal)


class ComposerAnchorTests(unittest.TestCase):
    def setUp(self):
        self.window = (0, 0, 3072, 1824)
        self.body = (873, 1618, 2199, 1796)
        self.editor = node(anchor._EDIT, (895, 1644, 2178, 1717),
                           "和 Codex 一起工作", "ProseMirror ProseMirror-focused")
        self.add = node(anchor._BUTTON, (888, 1730, 939, 1781), "添加文件等内容",
                        "h-token-button-composer aspect-square")
        self.permissions = node(anchor._BUTTON, (938, 1730, 1049, 1781), "更改权限",
                                "h-token-button-composer", "radix-permissions")
        self.context = node(anchor._IMAGE, (1880, 1741, 1910, 1771), "上下文用量：65%")
        self.model = node(anchor._BUTTON, (1916, 1730, 2070, 1781), "6.2 Astra 中",
                          "h-token-button-composer min-w-0", "radix-model")
        self.nodes = [self.editor, self.add, self.permissions, self.context, self.model]

    def gap(self, nodes):
        return anchor._toolbar_gap(self.window, self.body, self.editor.rect, nodes)

    def test_live_layout_fixture_caps_at_context_icon(self):
        self.assertEqual(self.gap(self.nodes), (1055, 1730, 1874, 1781))

    def test_context_absent_uses_model_boundary(self):
        self.assertEqual(self.gap([self.editor, self.add, self.permissions, self.model]),
                         (1055, 1730, 1910, 1781))

    def test_stop_action_must_belong_to_composer_toolbar(self):
        gap = self.gap(self.nodes)
        stop = node(anchor._BUTTON, (2130, 1730, 2181, 1781), '停止',
                    'size-token-button-composer bg-composer-primary')
        self.assertTrue(anchor._composer_running([*self.nodes, stop], gap))
        self.assertFalse(anchor._composer_running([*self.nodes, replace(stop, name='发送')], gap))
        self.assertFalse(anchor._composer_running([*self.nodes, replace(stop, rect=(2130, 700, 2181, 751))], gap))
        self.assertFalse(anchor._composer_running([*self.nodes, replace(stop, class_name='other-button')], gap))

    def test_negative_virtual_screen_origin(self):
        def translate(rect):
            return tuple(value + delta for value, delta in zip(rect, (-3100, -300, -3100, -300)))

        shifted = [replace(n, rect=translate(n.rect)) for n in self.nodes]
        self.assertEqual(anchor._toolbar_gap(translate(self.window), translate(self.body),
                                            shifted[0].rect, shifted),
                         translate((1055, 1730, 1874, 1781)))

    def test_any_visible_occupant_inside_gap_blocks_anchor(self):
        for kind in (anchor._BUTTON, 50020, 50026):
            with self.subTest(kind=kind):
                occupant = node(kind, (1300, 1745, 1400, 1770))
                self.assertIsNone(self.gap([*self.nodes, occupant]))
        just_above = node(50020, (1300, 1710, 1400, 1730))
        self.assertEqual(self.gap([*self.nodes, just_above]), (1055, 1730, 1874, 1781))

    def test_too_narrow_or_wrapped_toolbar_is_rejected(self):
        narrow_context = replace(self.context, rect=(1118, 1741, 1148, 1771))
        self.assertIsNone(self.gap([self.editor, self.add, self.permissions, narrow_context, self.model]))
        wrapped = replace(self.model, rect=(1916, 1755, 2070, 1781))
        self.assertIsNone(self.gap([self.editor, self.add, self.permissions, self.context, wrapped]))

    def test_partial_toolbar_and_confirmed_obstruction_are_distinct(self):
        def snapshot(nodes):
            return anchor._toolbar_gap(self.window, self.body, self.editor.rect, nodes, blocked=anchor.BLOCKED)

        self.assertIsNone(snapshot(self.nodes[:-1]))
        occupant = node(anchor._BUTTON, (1300, 1745, 1400, 1770))
        self.assertIs(snapshot([*self.nodes, occupant]), anchor.BLOCKED)

    def test_missing_or_ambiguous_model_identity_is_rejected(self):
        self.assertIsNone(self.gap(self.nodes[:-1]))
        self.assertIsNone(self.gap([*self.nodes, replace(self.model, automation_id="radix-other")]))
        self.assertIsNone(self.gap([*self.nodes[:-1], replace(self.model, automation_id="")]))

    def test_modal_or_menu_blocks_editor_candidate(self):
        editor = (object(), self.editor)
        self.assertEqual(anchor._eligible_editor([editor], self.window), editor)
        for role in ("dialog", "alertdialog", "menu"):
            with self.subTest(role=role):
                blocker = node(50026, (1200, 550, 1900, 900), role=role, modal=role == "dialog")
                self.assertIsNone(anchor._eligible_editor([editor, (object(), blocker)], self.window))
        side_dialog = node(50026, (2200, 1300, 3000, 1500), role="dialog")
        self.assertEqual(anchor._eligible_editor([editor, (object(), side_dialog)], self.window), editor)
        self.assertIsNone(anchor._eligible_editor([editor, (object(), self.editor)], self.window))
        self.assertIsNone(anchor._eligible_editor([editor], (0, 0, 1000, 500)))

    def test_split_anchors_to_main_editor_even_when_right_editor_has_focus(self):
        main = (object(), replace(self.editor, class_name='ProseMirror'))
        agent = (object(), replace(self.editor, rect=(2260, 1600, 3020, 1717)))
        overlapping = (object(), replace(agent[1], rect=(2130, 1600, 3020, 1717)))
        self.assertEqual(anchor._eligible_editor([agent, main], self.window), main)
        self.assertEqual(anchor._eligible_editor([main, agent], self.window), main)
        self.assertEqual(anchor._eligible_editor([main], self.window), main)
        self.assertIsNone(anchor._eligible_editor([main, overlapping], self.window))

    def test_auxiliary_composer_is_distinct_from_transient_missing_editor(self):
        self.assertIsNot(anchor.ABSENT, anchor.NO_MAIN)

    def test_only_one_native_active_chat_row_can_supply_the_title(self):
        row = node(50026, (1, 1, 3, 3), 'Selected chat', anchor._ACTIVE_CHAT_CLASS, role='group')
        other = replace(row, name='Tab', role='button')
        self.assertEqual(anchor.selected_chat_title([row, other]), 'Selected chat')
        self.assertIsNone(anchor.selected_chat_title([row, replace(row, name='Other chat')]))
        self.assertIsNone(anchor.selected_chat_title([other]))

    def test_project_new_chat_is_distinguished_from_an_existing_transcript(self):
        uia = object.__new__(anchor._UIA)
        uia.check_time = lambda: None
        uia.walker = uia.cache = None
        uia.string = lambda element, slot: element
        shared = ['relative', 'flex w-full flex-col gap-2 relative', 'contents']
        for parents, expected in (
            ([*shared, 'relative z-20 pt-1.5 pb-4', 'group/home-composer-layout relative flex'], True),
            ([*shared, '[container-name:home-main-content] overflow-y-auto'], True),
            ([*shared, 'footer-A8ZvKV bottom-0', 'thread-scroll-container peer/transcript'], False),
        ):
            with self.subTest(parents=parents), ExitStack() as stack:
                with patch.object(anchor, '_get', side_effect=parents), patch.object(anchor, '_owned', nullcontext):
                    self.assertIs(uia._home_composer(stack, object()), expected)

    def test_invalid_hwnd_returns_none(self):
        for hwnd in (0, -1, None, "1249640"):
            with self.subTest(hwnd=hwnd):
                self.assertIsNone(anchor.find_composer_anchor(hwnd))

    @unittest.skipUnless(sys.platform == "win32" and os.environ.get("COMPOSER_ANCHOR_HWND"),
                         "Set COMPOSER_ANCHOR_HWND to a live Codex window for the read-only smoke test")
    def test_live_window_on_background_thread(self):
        hwnd = int(os.environ["COMPOSER_ANCHOR_HWND"], 0)

        def read_at_dpi_context(context):
            user = C.WinDLL("user32")
            set_context = user.SetThreadDpiAwarenessContext
            set_context.argtypes = (C.c_void_p,)
            set_context.restype = C.c_void_p
            get_context = user.GetThreadDpiAwarenessContext
            get_context.argtypes = ()
            get_context.restype = C.c_void_p
            previous = set_context(context)
            self.assertTrue(previous)
            try:
                before = get_context()
                result = anchor.find_composer_anchor(hwnd)
                self.assertEqual(get_context(), before)
                return result
            finally:
                set_context(previous)

        with ThreadPoolExecutor(max_workers=1) as worker:
            unaware = worker.submit(read_at_dpi_context, -1).result()
            physical = worker.submit(read_at_dpi_context, -4).result()
        self.assertIsNotNone(physical)
        self.assertTrue(anchor._valid(physical))
        self.assertEqual(unaware, physical)


if __name__ == "__main__":
    unittest.main()
