"""Exercise the bridge and ownership handshake without opening Codex."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import launch
from usage_bridge import UsageBridge


class InlineBridgeTest(unittest.TestCase):
    def test_cold_rollout_continues_until_ready_without_summary_cache_delay(self):
        with TemporaryDirectory() as tmp:
            bridge = UsageBridge(Path(tmp), live=False)
            identity = '11111111-2222-3333-4444-555555555555'
            loading = {'session': {'id': identity}, 'status': 'loading'}
            ready = {'session': {'id': identity}, 'status': 'ready'}
            with patch.object(bridge.reader, 'load_session', side_effect=[loading, ready]) as read:
                first = bridge.summary(bridge.view(identity))
                second = bridge.summary(bridge.view(identity))
                self.assertTrue(first['usage_loading'])
                self.assertFalse(second['usage_loading'])
                self.assertEqual(bridge.summary(bridge.view(identity)), second)
                self.assertEqual(read.call_count, 2)
            with patch.object(bridge.reader, 'list_recent_sessions', return_value=[{'id': identity}]), patch.object(
                    bridge.reader, 'load_session', side_effect=[loading, loading, ready]) as read:
                bridge.prime_recent(budget=1)
                self.assertEqual(read.call_count, 3)
                self.assertTrue(all(call.args == (identity,) for call in read.call_args_list))

    def test_settings_reload_and_independent_switches(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'sidebar-config.json'
            bridge = UsageBridge(root, config, live=False)
            bridge.activity = Mock()
            identity = '11111111-2222-3333-4444-555555555555'
            for enabled, activity, live in ((True, True, True), (False, False, True),
                                            (True, True, False), (True, False, False)):
                with self.subTest(enabled=enabled, activity=activity, live=live):
                    config.write_text(json.dumps(dict(composerStyle='english', composerWeight='bold',
                        activityEnabled=activity, realtimeMetricsEnabled=live,
                        items=[dict(type='session_usage', enabled=enabled)])), encoding='utf-8')
                    bridge.next_config = 0
                    self.assertEqual(bridge.appearance(), dict(composerStyle='english', composerWeight='bold',
                                                               activityEnabled=activity))
                    self.assertEqual(bridge.enabled(), enabled)
                    bridge.tick()
                    bridge.activity.set_enabled.assert_called_with(live)
                    bridge.cached.clear()
                    with patch.object(bridge.reader, 'load_session', return_value=None), patch(
                            'usage_bridge.format_view', return_value={'turn_state': 'completed'}) as format_view:
                        self.assertEqual(bridge.view(identity)['dot_state'], 'completed' if activity else None)
                        self.assertIs(format_view.call_args.args[-1], bridge.activity if live else None)

    def test_managed_lease_follows_mount_and_switch(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            states = iter((True, False, False, True, True))
            controls = iter((True, True, False, True, True))
            status = {}
            reports, leases, switch_events = [], [], []

            class Renderer:
                def call(self, method, params):
                    if method == 'Target.getTargets':
                        status['mounted'] = next(states)
                        return {'targetInfos': [{'type': 'page', 'url': 'app://-/index.html', 'targetId': 'target'}]}
                    return {'sessionId': 'renderer'}

                def evaluate(self, sid, expression):
                    return {**status, 'generation': 1}

            bridge = Mock()
            bridge.appearance.return_value = {}
            bridge.enabled.side_effect = lambda: next(controls)
            bridge.summary.return_value = None
            proc = SimpleNamespace(pid=42, poll=Mock(side_effect=[None] * 5 + [0, 0]))

            def sent(dev, sid, method, value):
                if method == 'setEnabled':
                    switch_events.append(value)
                    if not value:
                        status['mounted'] = False

            with patch.object(launch, 'send', side_effect=sent), patch.object(launch, 'sleep'), patch.object(
                    launch, 'atomic_json', side_effect=lambda path, value: leases.append(len(reports))):
                self.assertEqual(launch.attach(proc, Renderer(), '', root, bridge, Mock(), reports.append,
                                               managed=True), 'closed')
            self.assertEqual(switch_events, [True, False, True])
            self.assertEqual(leases, [0, 3])
            self.assertEqual([row['enabled'] for row in reports], [True, True, False, True, True])

    def test_packaged_switch_without_legacy_module(self):
        with TemporaryDirectory() as tmp:
            home=Path(tmp)
            config=home/'sidebar-config.json'
            bridge=UsageBridge(home,config,live=False)
            for explicit, legacy, expected in ((True, None, True), (False, True, False),
                                                (None, False, False), (None, True, True)):
                state={'items': [] if legacy is None else [{'type':'session_usage','enabled':legacy}]}
                if explicit is not None: state['composerDisplayEnabled']=explicit
                config.write_text(json.dumps(state),encoding='utf-8')
                bridge.next_config=0
                bridge.appearance()
                self.assertIs(bridge.enabled(),expected)


if __name__ == '__main__':
    unittest.main()
