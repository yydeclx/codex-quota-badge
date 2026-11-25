import os
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from composer_anchor import ABSENT, BLOCKED, NO_MAIN, ComposerSample
from composer_usage import ComposerUsage, FADE_TIME, HOVER_DELAY, LEAVE_DELAY, PULSE_TIME, OBSTRUCTION_GRACE, IDENTITY_GRACE, popup_position, render_dot, render_summary, summary_layout
from session_usage_view import format_view
from test_session_usage_card import ENDED, SESSION, SNAPSHOT


class ComposerUsageTests(unittest.TestCase):
    def test_layout_event_wakes_the_idle_tracker_without_waiting_for_heartbeat(self):
        controls = ComposerUsage(SimpleNamespace(), SimpleNamespace())
        controls.next_anchor = 1.75
        self.assertIsNone(controls.anchor_wake_delay(1.))
        with patch('composer_usage.monotonic', return_value=1.1):
            controls.navigation_hint()
        self.assertTrue(controls.anchor_due(1.1))
        self.assertEqual(controls.anchor_wake_delay(1.1), 1)
        controls.busy = True
        self.assertIsNone(controls.anchor_wake_delay(1.1))
        self.assertIsNone(controls.anchor_wake_delay(1.5))

    def test_card_position_is_inside_the_host_and_avoids_toolbar(self):
        host = (100, 100, 900, 900)
        self.assertEqual(popup_position(host, (400, 800, 700, 860), (340, 330), 8), (380, 462))
        self.assertEqual(popup_position(host, (110, 130, 250, 156), (340, 330), 8), (108, 164))
        self.assertIsNone(popup_position(host, (110, 130, 250, 156), (800, 330), 8))

    def test_summary_keeps_entire_glyphs_inside_clickable_width(self):
        for scale in (1, 1.25, 1.5, 2, 2.5):
            for width in (44, 70, 118, 256):
                with self.subTest(scale=scale, width=width):
                    image = render_summary(scale, width, 26, '约 259 tok/s', '100%', '99.9%')
                    self.assertEqual(image.size, (round(width * scale), round(26 * scale)))
                    left, top, right, bottom = image.getchannel('A').point(lambda a: 255 if a > 80 else 0).getbbox()
                    self.assertTrue(0 < left < right < image.width)
                    self.assertTrue(0 < top < bottom < image.height)

    def test_hover_background_has_balanced_text_insets_at_different_speeds(self):
        for scale in (1, 1.25, 1.5, 2, 2.5):
            for speed in ('—', '52 tok/s', '约 100 tok/s'):
                with self.subTest(scale=scale, speed=speed):
                    image = render_summary(scale, 256, 26, speed, '36%', '84.8%', hover=True)
                    left, _, right, _ = image.getchannel('A').point(lambda a: 255 if a > 80 else 0).getbbox()
                    self.assertLessEqual(abs(left - (image.width - right)), round(2 * scale))
        narrow = render_summary(1, 75, 26, '52 tok/s', '36%', '84.8%')
        speed_only = render_summary(1, 75, 26, '52 tok/s', '—', '—')
        self.assertEqual(narrow.tobytes(), speed_only.tobytes())

    def test_status_dot_reuses_sidebar_health_for_the_selected_route(self):
        calls = []
        owner = SimpleNamespace(config={}, cached_indicator_map={'OCX': ((False, False), ('amber', 'retry'))},
                                chain_health=lambda provider: (calls.append(provider) or 'red', 'auth'))
        control = ComposerUsage(owner, None)
        self.assertEqual(control._indicator_state({'turn_state': 'active', 'status_provider': 'OCX'}), 'active')
        self.assertEqual(control._indicator_state({'turn_state': 'completed', 'status_provider': 'OCX'}), 'amber')
        self.assertEqual(control._indicator_state({'status_provider': 'SKG'}), 'red')
        self.assertEqual(calls, ['SKG'])
        self.assertEqual(control._indicator_state({'turn_state': 'completed'}), 'completed')
        self.assertIsNone(control._indicator_state({}))
        owner.config['activityEnabled'] = False
        self.assertIsNone(control._indicator_state({'turn_state': 'active', 'status_provider': 'OCX'}))

    def test_dot_and_text_stay_centered_together(self):
        for scale in (1, 1.5, 2, 2.5):
            line, left = summary_layout(scale, 256, 26, '54 tok/s', '53%', '85.2%', hover=True, with_dot=True)
            dot = render_dot(scale, False, 'completed')
            placed = line.copy()
            placed.alpha_composite(dot, (left, (line.height - dot.height) // 2))
            x0, _, x1, _ = placed.getchannel('A').point(lambda a: 255 if a > 80 else 0).getbbox()
            self.assertLessEqual(abs(x0 - (line.width - x1)), round(4 * scale))

    def test_existing_chat_keeps_usage_while_waiting_for_first_speed(self):
        control = ComposerUsage(SimpleNamespace(config={}), None)
        control.composer_running = True
        sample = {'session': 'existing', 'turn_state': 'active', 'metrics':
                  {'speed': '72 tok/s', 'speed_label': '上次输出速度', 'context': '42%', 'cache': '85.2%'}}
        expected = ('思考中', 'active', ('—', '42%', '85.2%'))
        self.assertEqual(control._summary_content(sample), expected)
        self.assertEqual(control._summary_content({'session': 'existing'}), expected)
        old_card, _ = summary_layout(1, 256, 26, '—', '42%', '85.2%', with_dot=True, status='思考中')
        no_values, _ = summary_layout(1, 256, 26, '—', '—', '—', with_dot=True, status='思考中')
        self.assertNotEqual(old_card.tobytes(), no_values.tobytes())
        self.assertEqual(control._summary_content({'session': 'different'}), ('思考中', 'active', ('—', '—', '—')))
        control.new_chat = True
        self.assertEqual(control._summary_content({}), ('正在启动', 'active', ('—', '—', '—')))

    def test_incomplete_identity_does_not_reselect_until_sustained(self):
        changes = []
        controls = ComposerUsage(SimpleNamespace(), SimpleNamespace(follow=lambda t: changes.append(t)))
        layout = ((0, 0, 900, 900), 96)
        location = (300, 820, 690, 860)
        controls._accept_observation(ComposerSample(location, 'A'), layout, 1.)
        controls._accept_observation(ComposerSample(location, None), layout, 1.1)
        controls._accept_observation(None, layout, 1.3)
        self.assertEqual(changes, ['A'])
        controls._accept_observation(ComposerSample(location, 'B'), layout, 1.4)
        controls._accept_observation(ComposerSample(location, None), layout, 2.)
        controls._accept_observation(None, layout, 2. + IDENTITY_GRACE + .01)
        self.assertEqual(changes, ['A', 'B', None])
        self.assertEqual(controls.anchor, location)

    def test_no_main_composer_hides_the_existing_badge_immediately(self):
        controls = ComposerUsage(SimpleNamespace(), SimpleNamespace())
        layout = ((0, 0, 900, 900), 96)
        location = (300, 820, 690, 860)
        controls._accept_observation(ComposerSample(location, 'A'), layout, 1.)
        self.assertEqual(controls.anchor, location)
        controls._accept_observation(NO_MAIN, layout, 1.1)
        self.assertIsNone(controls.anchor)
        self.assertIsNone(controls.anchor_layout)

    @unittest.skipUnless(os.name == 'nt', 'Requires a native Windows test window')
    def test_native_widget_lifecycle_and_action_routing(self):
        result = subprocess.run([sys.executable, __file__, '--native-scenario'], capture_output=True, timeout=18)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))


def _native_scenario():
    import ctypes as C
    from ctypes import wintypes as W
    from time import monotonic

    from native_badge import Win, WNDCLASS, WNDPROC

    w = Win()
    positions = (-12000, -12000, -11000, -11000)
    anchor = (positions[0] + 360, positions[1] + 800, positions[0] + 880, positions[1] + 850)

    class Monitor:
        def __init__(self):
            self.selected = SESSION['id']

        def select(self, identity):
            self.selected = identity

        def poll(self):
            pass

        def view(self, telemetry, activity):
            return format_view([SESSION], SNAPSHOT if self.selected == SESSION['id'] else None, self.selected, None, None)

    monitor = Monitor()
    owner = SimpleNamespace(win=w, config={'realtimeMetricsEnabled': False}, telemetry=None, activity=None)
    controls = ComposerUsage(owner, monitor, anchor_reader=lambda hwnd: anchor)

    @WNDPROC
    def proc(hwnd, msg, wp, lp):
        result = controls.handle(hwnd, msg, wp, lp)
        return w.u.DefWindowProcW(hwnd, msg, wp, lp) if result is None else result

    window_class = WNDCLASS(0, proc, 0, 0, w.instance, None, None, None, None, 'CodexQuotaNativeV2')
    assert w.u.RegisterClassW(C.byref(window_class))
    host = w.u.CreateWindowExW(0x08000080, 'STATIC', 'Isolated composer test host', 0x90000000,
                               positions[0], positions[1], 1000, 1000, None, None, w.instance, None)
    assert host

    def click(action):
        rect = next(box for box, kind in controls.actions if kind == action)
        x = round((rect[0] + rect[2]) * controls.card_scale / 2)
        y = round((rect[1] + rect[3]) * controls.card_scale / 2)
        w.u.SendMessageW(controls.popup, 0x202, 0, x | (y << 16))

    try:
        foreground = w.u.GetForegroundWindow()
        controls.host = host
        layout = (controls._bounds(host), w.u.GetDpiForWindow(host) or 96)

        def observe(value, measured_layout=layout, when=None):
            controls.result.put((controls.generation, measured_layout, monotonic() if when is None else when, value))
            controls.refresh(host)

        controls.result.put((controls.generation, layout, monotonic(), anchor))
        controls.next_anchor = float('inf')
        controls.refresh(host)
        assert controls.rect and w.u.IsWindowVisible(controls.window)
        assert controls.status() == {'visible':True, 'anchor':anchor, 'rect':controls.rect}
        left, top, right, bottom = controls.rect
        assert anchor[0] <= left < right <= anchor[2] and anchor[1] <= top < bottom <= anchor[3]
        assert abs(left + right - anchor[0] - anchor[2]) <= 1
        stable_hwnd = controls.window
        stable_rect = controls.rect
        # UIA can temporarily lose the composer while Codex swaps the chat DOM.
        monitor.selected = None
        observe(None)
        assert controls.window == stable_hwnd and controls.rect == stable_rect
        assert w.u.IsWindowVisible(stable_hwnd)
        observe(ABSENT)
        assert controls.rect == stable_rect  # a one-frame missing composer is not navigation away
        # Chat-specific controls can move the gap without resizing the pane.
        pane = anchor[0] - 25, anchor[2] + 75
        observe(ComposerSample(anchor, 'A', pane))
        wider = (anchor[0], anchor[1], anchor[2] + 28, anchor[3])
        observe(ComposerSample(wider, 'B', pane))
        wider_rect = (stable_rect[0] + 14, stable_rect[1], stable_rect[2] + 14, stable_rect[3])
        assert controls.rect == wider_rect and controls.window == stable_hwnd
        shifted = tuple(v - 36 if i % 2 else v for i, v in enumerate(wider))
        observe(ComposerSample(shifted, 'B', pane))
        assert controls.rect == tuple(v - 36 if i % 2 else v for i, v in enumerate(wider_rect))
        observe(ComposerSample(wider, 'B', pane))
        observe(BLOCKED, ((0, 0, 150, 120), 96))
        assert controls.rect == wider_rect  # ignore a result from an older layout
        monitor.selected = SESSION['id']
        observe(ComposerSample(anchor, 'A', pane))
        assert controls.rect == stable_rect and w.u.IsWindowVisible(stable_hwnd)
        started = monotonic()
        for index in range(4):
            observe(BLOCKED, when=started + index)
            assert controls.window == stable_hwnd and w.u.IsWindowVisible(stable_hwnd)
            observe(anchor, when=started + index + .1)
        observe(BLOCKED, when=started + 5)
        observe(BLOCKED, when=started + 5 + OBSTRUCTION_GRACE + .01)
        assert not w.u.IsWindowVisible(stable_hwnd)
        observe(anchor)
        assert controls.rect == stable_rect and w.u.IsWindowVisible(stable_hwnd)
        # A verified new chat replaces metrics on the same window. An
        # incomplete scan keeps that frame; returning to a chat restores it.
        observe(ComposerSample(anchor, None, pane, new_chat=True))
        ready_rect = controls.rect
        assert controls.dot_state == 'ready' and controls.dot_patch
        assert abs(ready_rect[0] + ready_rect[2] - anchor[0] - anchor[2]) <= 1
        assert ready_rect[2] - ready_rect[0] < stable_rect[2] - stable_rect[0]
        observe(None)
        assert controls.dot_state == 'ready' and controls.status()['rect'] == ready_rect
        # The first request can already be running while the rollout is absent.
        monitor.selected = None
        observe(ComposerSample(anchor, None, pane, new_chat=True, running=True))
        assert controls.summary_key[-1] == '正在启动' and controls.dot_state == 'active'
        assert controls.dot_patch and controls.window == stable_hwnd
        assert controls.rect == stable_rect and w.u.IsWindowVisible(stable_hwnd)
        old_sample = {'speed': '72 tok/s', 'speed_label': '上次输出速度'}
        assert controls._summary_content({'turn_state': 'active', 'metrics': old_sample})[0] == '正在启动'
        controls.new_chat = False
        live = {'speed': '约 80 tok/s', 'speed_label': '生成速度'}
        assert controls._summary_content({'turn_state': 'active', 'metrics': old_sample})[0] == '思考中'
        assert controls._summary_content({'session': 'other-test-chat', 'turn_state': 'active', 'metrics': live}) == (None, 'active', ('约 80 tok/s', '—', '—'))
        assert controls._summary_content({'turn_state': 'completed', 'metrics': live})[0] == '思考中'
        monitor.selected = SESSION['id']
        observe(ComposerSample(anchor, 'A', pane, running=False))
        assert controls.dot_state != 'ready' and controls.rect == stable_rect
        assert controls.window == stable_hwnd and w.u.IsWindowVisible(stable_hwnd)
        # Move the host without waiting for a new UIA observation.
        assert w.u.SetWindowPos(host, None, positions[0] + 30, positions[1] + 18, 0, 0, 0x15)
        controls.refresh(host)
        translated = tuple(value + (18 if i % 2 else 30) for i, value in enumerate(stable_rect))
        assert controls.status()['rect'] == translated and controls.window == stable_hwnd
        assert w.u.IsWindowVisible(stable_hwnd)
        left, top, _, _ = controls.rect
        assert w.u.SendMessageW(controls.window, 0x21, 0, 0) == 3
        w.u.SendMessageW(controls.window, 0x202, 0, 0)
        assert controls.open and w.u.IsWindowVisible(controls.popup)
        click(('usage_page', 'sessions'))
        monitor.selected = None
        click(('usage_select', SESSION['id']))
        assert monitor.selected == SESSION['id'] and controls.page == 'summary'
        click(('usage_page', 'turns'))
        click(('usage_turn', ENDED['id']))
        assert controls.page == 'turn' and controls.turn == ENDED['id']
        assert w.u.GetForegroundWindow() not in (host, controls.window, controls.popup)
        controls.fold()
        controls.motion = True
        started = monotonic()
        controls._pointer(controls.window, left, top, started)
        controls._pointer(controls.window, left, top, started + HOVER_DELAY / 2)
        assert not controls.open and controls.timer_ms == 15
        controls._pointer(controls.window, left, top, started + HOVER_DELAY + .001)
        surface = controls.card_surface
        assert controls.open and not controls.pinned and surface
        controls._present_popup(controls.transition[0])
        start_y = controls.popup_rect[1]
        assert start_y > controls.popup_target[1]
        controls._present_popup(controls.transition[0] + FADE_TIME / 2)
        assert 0 < controls.opacity < 1 and controls.card_surface is surface
        assert controls.popup_rect[0] == controls.popup_target[0]
        assert controls.popup_target[1] <= controls.popup_rect[1] < start_y
        controls._present_popup(controls.transition[0] + FADE_TIME + .001)
        assert controls.opacity == 1 and controls.popup_rect[1] == controls.popup_target[1]
        leave = monotonic()
        controls._pointer(None, left, top, leave)
        controls._pointer(None, left, top, leave + LEAVE_DELAY + .001)
        controls._present_popup(controls.transition[0] + FADE_TIME / 2)
        opacity = controls.opacity
        assert 0 < opacity < 1
        assert controls.popup_rect[1] > controls.popup_target[1]
        controls._pointer(controls.window, left, top, monotonic())
        assert controls.transition[1:] == (opacity, 1.) and controls.card_surface is surface
        w.u.SendMessageW(controls.window, 0x202, 0, 0)
        assert controls.pinned and controls.open
        controls._pointer(None, left, top, monotonic() + 3)
        assert controls.pinned and controls.left_at is None
        controls._present_popup(monotonic() + 1)
        w.u.SendMessageW(controls.window, 0x202, 0, 0)
        controls._present_popup(monotonic() + 1)
        controls._pointer(controls.window, left, top, monotonic() + 2)
        assert not controls.open and controls.timer_ms == 50 and controls.card_surface is None
        assert controls.dot_state == 'active' and controls.dot_patch
        original_summary = controls.summary_surface
        controls._present_dot(controls.pulse_start)
        peak = controls.dot_frame[1]
        controls._present_dot(controls.pulse_start + PULSE_TIME / 2)
        assert controls.dot_frame[0] == 'active' and controls.dot_frame[1] < peak
        assert controls.summary_surface is original_summary and controls.window == stable_hwnd
        for state in ('completed', 'amber', 'red'):
            controls.dot_state = state
            controls._present_dot(monotonic())
            controls._schedule()
            assert controls.dot_frame == (state, 240) and controls.timer_ms is None
        controls.motion = False
        w.u.SendMessageW(controls.window, 0x202, 0, 0)
        assert controls.open and controls.pinned and controls.opacity == 1 and controls.transition is None
        assert w.u.GetForegroundWindow() not in (host, controls.window, controls.popup)
        controls.invalidate()
        assert not controls.rect and not w.u.IsWindowVisible(controls.window)
        assert not controls.open and not w.u.IsWindowVisible(controls.popup)
        assert controls.timer_ms is None
        assert controls.status() == {'visible':False, 'anchor':None, 'rect':None}
    finally:
        controls.destroy()
        w.u.DestroyWindow(host)


if __name__ == '__main__':
    if sys.argv[1:] == ['--native-scenario']:
        _native_scenario()
    else:
        unittest.main()
