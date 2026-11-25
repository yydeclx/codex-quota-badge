import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from session_usage_card import render_session_card
from session_usage_view import SessionUsageMonitor, format_view, turn_timing
from sidebar_data import validate_config


SESSION = {'id': 'one', 'innerId': 'rollout-one', 'title': '验证贴合 Codex 字体的中文长会话名', 'model': 'OCX/gpt-6.2-sol'}
ACTIVE = {'id': 'active', 'started_at': 3000, 'completed_at': None, 'status': 'active'}
ENDED = {'id': 'ended', 'started_at': 1000, 'completed_at': 3000, 'status': 'completed'}
TOTAL = {'input_tokens': 371000, 'cached_input_tokens': 305000, 'output_tokens': 10800,
         'reasoning_output_tokens': 2710, 'total_tokens': 381800}
LATEST = {'input_tokens': 35001, 'cached_input_tokens': 28600, 'output_tokens': 1200,
          'reasoning_output_tokens': 420, 'total_tokens': 36201}
SNAPSHOT = {'session': SESSION, 'status': 'ready', 'latest': LATEST, 'total': TOTAL,
            'context_window': 256000, 'context_used': 36201, 'active_turn_id': ACTIVE['id'],
            'turns': [{**ACTIVE, 'usage': LATEST}, {**ENDED, 'usage': TOTAL}]}


class SessionUsageCardTests(unittest.TestCase):
    def test_live_and_completed_timing_are_scoped_to_turn_and_session(self):
        digest = hashlib.sha256(SESSION['innerId'].encode()).hexdigest()[:32]
        done = {'conversationId': digest, 'timestamp': 1250, 'durationMs': 1500,
                'firstOutputMs': 250, 'lastOutputMs': 1400, 'decodeTokPerSecond': 52}
        other = {**done, 'conversationId': 'other-thread', 'timestamp': 1750, 'decodeTokPerSecond': 999}
        current = {'conversationId': SESSION['id'], 'startedAt': 3010, 'durationMs': 1750,
                   'firstOutputMs': 450, 'lastOutputMs': 1700, 'outputTokens': 125, 'estimated': True}
        live = {'supportsLive': True, 'requests': [current, {**current, 'conversationId': 'other', 'startedAt': 3500}]}
        self.assertEqual(turn_timing(SESSION, ENDED, [done, other], live)['speed'], '52 tok/s')
        self.assertEqual(turn_timing(SESSION, ACTIVE, [done, other], live)['speed'], '约 100 tok/s')
        self.assertEqual(turn_timing(SESSION, ACTIVE, [done, other], live)['first'], '0.5s')
        self.assertEqual(turn_timing(SESSION, ACTIVE, [done, other]), {})
        self.assertEqual(turn_timing(SESSION, {**ENDED, 'completed_at': None}, [done], live), {})

    def test_summary_uses_same_chat_completed_speed_until_live_speed_is_measured(self):
        finished = {'conversationId': hashlib.sha256(SESSION['innerId'].encode()).hexdigest()[:32],
                    'timestamp': 1250, 'durationMs': 1500, 'outcome': 'completed', 'decodeTokPerSecond': 52}
        other = {**finished, 'conversationId': 'other-thread', 'timestamp': 2450, 'decodeTokPerSecond': 999}
        failed = {**finished, 'timestamp': 2200, 'outcome': 'aborted', 'decodeTokPerSecond': 777}
        telemetry = Mock(session_samples=lambda _: [finished, other, failed])
        request = {'conversationId': SESSION['id'], 'startedAt': 3010, 'durationMs': 1750,
                   'firstOutputMs': 450, 'lastOutputMs': 1700, 'outputTokens': 125, 'estimated': True}

        def view_with(rows):
            activity = Mock(details=lambda _: {'supportsLive': True, 'requests': rows})
            return format_view([SESSION], SNAPSHOT, SESSION['id'], telemetry, activity)

        waiting = view_with([{'conversationId': SESSION['id'], 'startedAt': 3010, 'durationMs': 600}])
        self.assertEqual(waiting['metrics']['speed'], '52 tok/s')
        self.assertEqual(waiting['metrics']['speed_label'], '上次输出速度')
        self.assertIn('等待', waiting['metrics']['timing'])
        self.assertEqual(waiting['turns'][0]['metrics'][5][1], '—')
        self.assertEqual(view_with([request])['metrics']['speed'], '约 100 tok/s')
        self.assertEqual(view_with([])['metrics']['speed'], '52 tok/s')
        self.assertEqual(format_view([], None, SESSION['id'], telemetry, None)['summary_speed'], '—')
        self.assertEqual(format_view([], {**SNAPSHOT, 'status': 'loading'}, SESSION['id'], telemetry, None)['summary_speed'], '52 tok/s')
        self.assertEqual(format_view([], None, 'other-thread', telemetry, None)['summary_speed'], '999 tok/s')

    def test_turn_indicator_does_not_inherit_a_previous_or_unknown_chat_state(self):
        self.assertEqual(format_view([], SNAPSHOT, SESSION['id'], None, None)['turn_state'], 'active')
        for state in ('completed', 'aborted', 'failed'):
            last = {**ENDED, 'status': state}
            snapshot = {**SNAPSHOT, 'active_turn_id': None, 'turns': [last]}
            view = format_view([], snapshot, SESSION['id'], None, None)
            self.assertEqual(view['status_provider'], 'OCX')
            self.assertEqual(view.get('turn_state'), 'completed' if state == 'completed' else None)
        self.assertNotIn('turn_state', format_view([], None, 'unverified', None, None))

    def test_dark_card_uses_the_same_action_geometry(self):
        view = format_view([SESSION], SNAPSHOT, SESSION['id'], None, None)
        light, light_actions = render_session_card(1.5, view)
        dark, dark_actions = render_session_card(1.5, view, dark=True)
        self.assertEqual(light_actions, dark_actions)
        self.assertEqual(light.size, dark.size)
        self.assertLess(dark.getpixel((40, 52))[0], light.getpixel((40, 52))[0])

    def test_view_has_distinct_context_and_cache_denominators(self):
        view = format_view([SESSION], SNAPSHOT, SESSION['id'], None, None)
        self.assertEqual(view['metrics']['context'], '14%')
        self.assertEqual(view['metrics']['cache'], '82.2%')
        self.assertEqual(view['metrics']['last_cache'], '最近调用 81.7%')
        self.assertEqual(view['metrics']['speed'], '—')
        self.assertEqual(view['turns'][1]['id'], ENDED['id'])

    def test_large_values_and_chinese_action_areas_fit_at_supported_dpi(self):
        view = format_view([SESSION], SNAPSHOT, SESSION['id'], None, None)
        for scale in (1, 1.25, 1.5, 2, 2.5):
            for page, turn, count in (('summary', None, 2), ('sessions', None, 2), ('turns', None, 3), ('turn', ENDED['id'], 1)):
                with self.subTest(dpi=scale, page=page):
                    image, actions = render_session_card(scale, view, page=page, turn_id=turn)
                    self.assertEqual(len(actions), count)
                    for (left, top, right, bottom), _ in actions:
                        self.assertTrue(0 < left * scale < right * scale < image.width)
                        self.assertTrue(0 < top * scale < bottom * scale < image.height)

    def test_selection_persists_and_ignores_previous_worker_result(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'selection.json'
            monitor = SessionUsageMonitor(Mock(), selection_path=path, clock=lambda: 1)
            monitor.select(SESSION['id'])
            previous = monitor.generation
            monitor.select('two')
            monitor.results.put((previous, [SESSION], SESSION['id'], SNAPSHOT))
            monitor.next_poll = 10
            monitor.poll()
            self.assertEqual(monitor.requested, 'two')
            self.assertIsNone(monitor.snapshot)
            self.assertEqual(SessionUsageMonitor(Mock(), selection_path=path).requested, 'two')

    def test_follow_visible_chat_rejects_late_old_data_and_ignores_manual_selection(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'selection.json'
            path.write_text('"old-chat"', encoding='utf-8')
            monitor = SessionUsageMonitor(Mock(), selection_path=path, clock=lambda: 1, follow_visible=True)
            self.assertIsNone(monitor.requested)
            monitor.follow('会话 A')
            generation_a = monitor.generation
            monitor.follow('会话 B')
            generation_b = monitor.generation
            monitor.next_poll = float('inf')
            monitor.results.put((generation_a, None, SESSION['id'], SNAPSHOT, True))
            monitor.poll()
            self.assertNotIn('metrics', monitor.view(None))
            other = {**SNAPSHOT, 'session': {**SESSION, 'id': 'b'}, 'context_used': 65536}
            monitor.results.put((generation_b, None, 'b', other, True))
            monitor.poll()
            self.assertEqual(monitor.view(None)['metrics']['context'], '26%')
            monitor.follow(None)
            self.assertNotIn('metrics', monitor.view(None))
            self.assertEqual(path.read_text(encoding='utf-8'), '"old-chat"')

    def test_partial_snapshot_uses_cache_only_for_freshly_verified_id(self):
        monitor = SessionUsageMonitor(Mock(), clock=lambda: 1., follow_visible=True)
        monitor.follow('A')
        monitor._accept_visible_snapshot(SESSION['id'], SNAPSHOT)
        monitor._accept_visible_snapshot(SESSION['id'], {**SNAPSHOT, 'status': 'loading'})
        self.assertIs(monitor.snapshot, SNAPSHOT)
        monitor.follow('B')
        self.assertIsNone(monitor.snapshot)
        monitor._accept_visible_snapshot('B', {**SNAPSHOT, 'status': 'loading'})
        self.assertIsNone(monitor.snapshot)
        monitor._accept_visible_snapshot(None, None)
        self.assertIsNone(monitor.snapshot)

    def test_prepared_switch_publishes_the_new_chat_without_an_empty_frame(self):
        monitor = SessionUsageMonitor(Mock(), clock=lambda: 1., follow_visible=True)
        monitor.follow('A', (SESSION['id'], SNAPSHOT))
        self.assertEqual(monitor.view(None)['metrics']['context'], '14%')
        old_generation = monitor.generation
        other = {**SNAPSHOT, 'session': {**SESSION, 'id': 'B'}, 'context_used': 65536}
        monitor.follow('B', ('B', other))
        self.assertEqual(monitor.view(None)['metrics']['context'], '26%')
        monitor.next_poll = float('inf')
        monitor.results.put((old_generation, None, SESSION['id'], SNAPSHOT, True))
        monitor.poll()
        self.assertEqual(monitor.snapshot['session']['id'], 'B')
        monitor.follow('Unverified', (None, None))
        self.assertNotIn('metrics', monitor.view(None))

    def test_manager_registry_accepts_module(self):
        cfg = {'version': 1, 'items': [{'id': 'session_usage', 'type': 'session_usage', 'label': '会话用量', 'enabled': False}]}
        self.assertFalse(validate_config(cfg)['items'][0]['enabled'])


if __name__ == '__main__':
    unittest.main()
