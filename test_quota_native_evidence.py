"""Native quota fixtures only; never open the user's Codex database."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import quota_native_evidence as evidence


class NativeEvidenceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name)
        self.now = 1793589000
        self.reset = self.now + 86400
        self.at = self.now - 60
        self.identity = hashlib.sha256(b'opencodex-main-quota-v1\0fixture-account').hexdigest()
        evidence._FILE_CACHE.clear()
        evidence._PATH_CACHE.clear()
        clock = patch.object(evidence.time, 'time', return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def event(self, at=None, used=40, reset=None, minutes=10080, limit_id='codex'):
        stamp = self.at if at is None else at
        return {'timestamp': dt.datetime.fromtimestamp(stamp, dt.timezone.utc).isoformat(),
                'type': 'event_msg', 'payload': {'type': 'token_count', 'rate_limits': {
                    'limit_id': limit_id, 'secondary': {'window_minutes': minutes,
                    'used_percent': used, 'resets_at': self.reset if reset is None else reset}}}}

    def write(self, rows=None, creator='fixture-account', name='rollout.jsonl'):
        path = self.home/name
        meta = {'type': 'session_meta', 'payload': {'creator_account_id': creator}}
        path.write_bytes(b''.join(json.dumps(row).encode()+b'\n'
                                 for row in [meta, *(rows if rows is not None else [self.event()])]))
        return path

    def collect(self, paths, **overrides):
        args = dict(identity_key=self.identity, reset=self.reset,
                    since=self.now-3600, until=self.now)
        args.update(overrides)
        return evidence.collect_native_samples(self.home, paths=paths, **args)

    def test_identity_and_anonymous_output(self):
        path = self.write()
        result = self.collect(lambda home: [home/path.name])
        self.assertEqual(result, [{'at': self.at, 'remaining': 60, 'reset': self.reset,
                                   'source': 'native-response'}])
        self.assertNotIn('fixture-account', repr(evidence._FILE_CACHE))
        self.assertNotIn('fixture-account', repr(result))

    def test_identity_mismatch_missing_and_invalid_header(self):
        for creator in ('other-account', '', None, 42):
            with self.subTest(creator=creator):
                self.assertEqual(self.collect([self.write(creator=creator)]), [])
        for raw in (b'[]\n', b'invalid\n', b'{"type":"event_msg"}\n', b'{}'):
            path = self.home/'bad.jsonl'
            path.write_bytes(raw)
            self.assertEqual(self.collect([path]), [])

    def test_reset_jitter_keeps_raw_reset_and_rejects_mismatch(self):
        rows = [self.event(reset=self.reset+offset) for offset in (-121, -120, 0, 120, 121)]
        result = self.collect([self.write(rows)])
        self.assertEqual([r['reset'] for r in result], [self.reset-120, self.reset, self.reset+120])

    def test_week_duration_and_known_limit_ids(self):
        for minutes in (300, 10079, None, True, '10080'):
            self.assertEqual(self.collect([self.write([self.event(minutes=minutes)])]), [])
        for limit_id in ('spark', 'other', '', 123):
            self.assertEqual(self.collect([self.write([self.event(limit_id=limit_id)])]), [])
        row = self.event()
        limits = row['payload']['rate_limits']
        limits['primary'] = limits.pop('secondary')
        self.assertEqual(len(self.collect([self.write([row])])), 1)
        for limit_id in (None, 'codex'):
            self.assertEqual(len(self.collect([self.write([self.event(limit_id=limit_id)])])), 1)
        row = self.event()
        del row['payload']['rate_limits']['limit_id']
        self.assertEqual(len(self.collect([self.write([row])])), 1)

    def test_real_primary_weekly_shape_preserves_all_guards(self):
        def primary(**kwargs):
            row = self.event(**kwargs)
            limits = row['payload']['rate_limits']
            limits['primary'] = limits['secondary']
            limits['secondary'] = None
            return row
        rows = [primary(at=self.at+i, used=used, reset=self.reset+(i % 2))
                for i, used in enumerate((38, 39, 40))]
        result = self.collect([self.write(rows)])
        self.assertEqual([r['remaining'] for r in result], [62, 61, 60])
        self.assertEqual(self.collect([self.write(rows, creator='other-account')]), [])
        for kwargs in ({'reset': self.reset+121}, {'minutes': 300},
                       {'limit_id': 'spark'}, {'used': float('nan')},
                       {'at': self.now+1}, {'at': self.now-3601}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.collect([self.write([primary(**kwargs)])]), [])

    def test_weekly_slot_swap_duplicate_and_conflicting_windows(self):
        row = self.event()
        limits = row['payload']['rate_limits']
        limits['primary'] = {'window_minutes': 300, 'used_percent': 99,
                             'resets_at': self.now+300}
        self.assertEqual(self.collect([self.write([row])])[0]['remaining'], 60)
        limits['primary'], limits['secondary'] = limits['secondary'], limits['primary']
        self.assertEqual(self.collect([self.write([row])])[0]['remaining'], 60)
        limits['secondary'] = dict(limits['primary'])
        self.assertEqual(len(self.collect([self.write([row])])), 1)
        limits['secondary']['used_percent'] = 41
        self.assertEqual(self.collect([self.write([row])]), [])

    def test_range_future_expired_and_timestamp(self):
        rows = [self.event(at=at) for at in (self.now-3601, self.now-3600, self.now, self.now+1)]
        result = self.collect([self.write(rows)], until=self.now+100)
        self.assertEqual([r['at'] for r in result], [self.now-3600, self.now])
        self.assertEqual(self.collect([self.write([self.event(reset=self.at)])], reset=self.at), [])
        old = self.reset-evidence.WEEK_SECONDS-1
        self.assertEqual(self.collect([self.write([self.event(at=old)])], since=old-1), [])
        for stamp in ('invalid', '2026-11-02T01:00:00', None, self.at):
            row = self.event()
            row['timestamp'] = stamp
            self.assertEqual(self.collect([self.write([row])]), [])

    def test_finite_values_invalid_json_and_partial_line(self):
        for used in (-1, 101, float('nan'), float('inf'), True, None, '40'):
            self.assertEqual(self.collect([self.write([self.event(used=used)])]), [])
        for reset in (float('nan'), float('inf'), True, '123'):
            self.assertEqual(self.collect([self.write([self.event(reset=reset)])]), [])
        path = self.write([self.event(used=0), self.event(used=100)])
        with path.open('ab') as stream:
            stream.write(b'invalid\n'+json.dumps(self.event(used=20)).encode())
        self.assertEqual(sorted(r['remaining'] for r in self.collect([path])), [0, 100])
        for kwargs in ({'reset': float('nan')}, {'since': float('inf')},
                       {'until': False}, {'identity_key': 'invalid'}, {'since': self.now+1}):
            self.assertEqual(self.collect([path], **kwargs), [])

    def test_fastpath_refilters_and_returns_independent_results(self):
        path = self.write()
        first = self.collect([path])
        first[0]['remaining'] = 999
        with patch.object(Path, 'open', side_effect=AssertionError('cache should not read')):
            self.assertEqual(self.collect([path])[0]['remaining'], 60)
            self.assertEqual(self.collect([path], since=self.now), [])
            self.assertEqual(self.collect([path], reset=self.reset+121), [])
            self.assertEqual(self.collect([path], identity_key='a'*64), [])

    def test_rotation_append_and_identity_cache(self):
        path = self.write()
        self.collect([path])
        with path.open('ab') as stream:
            stream.write(json.dumps(self.event(at=self.at+1, used=41)).encode()+b'\n')
        self.assertEqual(len(self.collect([path])), 2)
        replacement = self.write([self.event(used=42)], name='replacement.jsonl')
        replacement.replace(path)
        self.assertEqual(self.collect([path])[0]['remaining'], 58)
        other = self.write(creator='other-account', name='other.jsonl')
        self.assertEqual(self.collect([other]), [])
        other_key = hashlib.sha256(b'opencodex-main-quota-v1\0other-account').hexdigest()
        self.assertEqual(len(self.collect([other], identity_key=other_key)), 1)

    def test_budget_files_deadline_and_large_head(self):
        paths = [self.write([self.event(at=self.at+i)], name=f'{i}.jsonl') for i in range(17)]
        self.assertEqual(len(self.collect(paths)), 16)
        evidence._FILE_CACHE.clear()
        with patch.object(evidence, 'TOTAL_BYTES', 1):
            self.assertEqual(self.collect(paths), [])
        with patch.object(evidence, 'TIME_BUDGET', 0):
            self.assertEqual(self.collect(paths), [])
        huge = self.home/'huge.jsonl'
        huge.write_bytes(b' ' * evidence.HEAD_BYTES + b'\n' + json.dumps(self.event()).encode()+b'\n')
        self.assertEqual(self.collect([huge]), [])

    def test_sqlite_schema_readonly_order_limit_and_fastpath(self):
        paths = [self.write([self.event(at=self.at+i)], name=f'{i}.jsonl') for i in range(17)]
        database = self.home/'state_5.sqlite'
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE threads (rollout_path TEXT, updated_at INTEGER)')
            db.executemany('INSERT INTO threads VALUES (?, ?)', [(str(p), i) for i, p in enumerate(paths)])
        db.close()
        before = database.read_bytes()
        real_connect = sqlite3.connect
        calls = []
        def connect(database_uri, **kwargs):
            calls.append(database_uri)
            return real_connect(database_uri, **kwargs)
        with patch.object(evidence.sqlite3, 'connect', side_effect=connect):
            result = self.collect(None)
        self.assertEqual(len(result), 16)
        self.assertEqual(result[0]['at'], self.at+1)
        self.assertTrue(all('?mode=ro' in call for call in calls))
        self.assertEqual(before, database.read_bytes())
        with patch.object(evidence.sqlite3, 'connect', side_effect=AssertionError('index cache')):
            self.assertEqual(self.collect(None), result)

    def test_missing_database_schema_and_duplicate_paths(self):
        self.assertEqual(self.collect(None), [])
        self.assertFalse((self.home/'state_5.sqlite').exists())
        with sqlite3.connect(self.home/'state_5.sqlite') as db:
            db.execute('CREATE TABLE threads (rollout_path TEXT)')
        db.close()
        self.assertEqual(self.collect(None), [])
        path = self.write([self.event(), self.event()])
        self.assertEqual(len(self.collect([path, path])), 1)


if __name__ == '__main__':
    unittest.main()
