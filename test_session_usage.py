import json
import os
import sqlite3
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import session_usage
from session_usage import SessionUsageAdapter


THREAD = '01900000-0000-7000-8000-000000000001'
OTHER = '01900000-0000-7000-8000-000000000002'
ROTATED = '01900000-0000-7000-8000-000000000003'
STAMP = '2026-11-23T02:03:01.250Z'
WINDOW = 128_000
PRIVATE = 'private-user-prompt-fixture'


def line(kind, **payload):
    return json.dumps({'timestamp': STAMP, 'type': kind, 'payload': payload}).encode() + b'\n'


def event(kind, **payload):
    return line('event_msg', type=kind, **payload)


def usage(input_tokens, output_tokens, cached=0, reasoning=0, written=0, *, total=None):
    return dict(input_tokens=input_tokens, cached_input_tokens=cached, cache_write_input_tokens=written,
                output_tokens=output_tokens, reasoning_output_tokens=reasoning,
                total_tokens=input_tokens + output_tokens if total is None else total)


def count(latest=None, total=None, window=WINDOW):
    info = dict(last_token_usage=latest, total_token_usage=total, model_context_window=window)
    return event('token_count', info=info, rate_limits={'secret': PRIVATE})


def record(turn, response, latest, turn_total, thread_total, thread=THREAD):
    return dict(thread_id=thread, turn_id=turn, response_id=response,
                usage=latest, turn_token_usage=turn_total, thread_token_usage=thread_total)


def meta(thread=THREAD, **extra):
    return line('session_meta', id=thread, session_id=thread, model_provider='local-provider',
                instructions=PRIVATE, **extra)


def index_line(thread, title):
    return json.dumps({'id': thread, 'thread_name': title}).encode() + b'\n'


class SessionUsageTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.folder = self.home / 'sessions' / date.today().strftime('%Y/%m/%d')
        self.folder.mkdir(parents=True)
        self.db = self.home / 'state_5.sqlite'
        self.index = self.home / 'session_index.jsonl'
        self.adapter = SessionUsageAdapter(self.home)
        self.rollout = self.path(THREAD)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('''CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT,
                         title TEXT, name TEXT, first_user_message TEXT, source TEXT, archived INTEGER,
                         attached_thread_parent_id TEXT, model_provider TEXT, model TEXT,
                         recency_at_ms INTEGER, updated_at_ms INTEGER)''')

    def path(self, thread, inner=None):
        suffix = f'{thread}_{inner}' if inner else thread
        return self.folder / f'rollout-2026-11-23T02-03-01-{suffix}.jsonl'

    def register(self, path=None, thread=THREAD, *, title=PRIVATE, name=None, source='vscode',
                 archived=False, attached=None, recency=3000, updated=2000):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('INSERT OR REPLACE INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                         (thread, str(path or self.rollout), title, name, PRIVATE + ' continued',
                          source, archived, attached, 'db-provider', 'db-model', recency, updated))

    def append(self, *lines, path=None):
        with (path or self.rollout).open('ab') as f:
            f.write(b''.join(lines))

    def load(self, thread=THREAD, **options):
        return self.adapter.load_session(thread, **options)

    def test_visible_title_must_match_exactly_one_chat_even_with_older_duplicates(self):
        self.register(name='  Current Chat  ')
        self.assertEqual(self.adapter.identify_visible_chat('Current Chat'), THREAD)
        self.assertIsNone(self.adapter.identify_visible_chat('Current'))
        self.register(self.path(OTHER), OTHER, title='CURRENT CHAT', archived=True, recency=1)
        self.assertIsNone(self.adapter.identify_visible_chat('Current Chat'))

    def test_catalog_reads_safe_metadata_from_sqlite_and_exact_lookup_can_select_an_older_id(self):
        self.register(name='Named thread')
        self.register(self.path(OTHER), OTHER, recency=4000, title='Generated label')
        self.register(self.path(ROTATED), ROTATED, source='{"subagent":"review"}', recency=9000)
        for name, options in [('archived', {'archived': True}), ('attached', {'attached': THREAD})]:
            self.register(self.folder / f'{name}.jsonl', name, recency=9000, **options)
        self.append(meta(), line('turn_context', turn_id='turn-1', model='rollout-model'))
        self.assertEqual(self.adapter.list_recent_sessions(1), [dict(id=OTHER, innerId=OTHER,
                         title='Generated label', provider='db-provider', model='db-model', updated_at=4000)])
        entry = self.load()['session']
        self.assertEqual(entry, dict(id=THREAD, innerId=THREAD, title='Named thread',
                                     provider='local-provider', model='rollout-model', updated_at=3000))
        self.assertEqual([r['id'] for r in self.adapter.list_recent_sessions(1000)], [OTHER, THREAD])
        self.assertEqual(self.adapter.list_recent_sessions(0), [])
        self.assertIsNone(self.load('unknown'))
        self.assertIsNone(self.load('../state_5.sqlite'))

    def test_native_prompt_preview_and_non_usage_properties_never_leave_the_adapter(self):
        self.register(title=PRIVATE + '…')
        sample = usage(80, 20, cached=10, reasoning=5)
        self.append(meta(), event('task_started', turn_id='t1', user_message=PRIVATE),
                    event('user_message', message=PRIVATE), line('response_item', content=PRIVATE),
                    count(sample, sample), event('task_complete', turn_id='t1', last_agent_message=PRIVATE))
        snapshot = self.load()
        self.assertIsNone(snapshot['session']['title'])
        encoded = json.dumps(snapshot)
        self.assertNotIn(PRIVATE, encoded)
        self.assertNotIn(str(self.home), encoded)
        self.assertEqual(snapshot['status'], 'ready')

    def test_legacy_counts_use_latest_for_context_and_do_not_charge_repeated_snapshots(self):
        self.register()
        first = usage(100, 30, cached=70, reasoning=12, written=4)
        second = usage(200, 60, cached=80, reasoning=25, written=6)
        total = usage(300, 90, cached=150, reasoning=37, written=10)
        self.append(meta(context_window=WINDOW), event('task_started', turn_id='t1', started_at=10),
                    count(first, first), count(first, first), event('token_count', info=None),
                    event('task_complete', turn_id='t1', completed_at=11),
                    event('task_started', turn_id='t2', started_at=20), count(first, first),
                    count(second, total))
        result = self.load()
        self.assertEqual(result['context_used'], 260)
        self.assertEqual(result['context_window'], WINDOW)
        self.assertEqual(result['latest']['uncached_input_tokens'], 120)
        self.assertEqual(result['latest']['non_reasoning_output_tokens'], 35)
        self.assertEqual(result['latest']['cache_write_input_tokens'], 6)
        self.assertEqual(result['latest']['total_tokens'], 260)
        self.assertEqual(result['total']['total_tokens'], 390)
        self.assertEqual(result['active_turn_id'], 't2')
        second_turn, first_turn = result['turns']
        self.assertEqual(second_turn, dict(id='t2', started_at=20000, completed_at=None,
                                           status='active', usage=result['latest'], calls=1))
        self.assertEqual(first_turn['usage']['total_tokens'], 130)
        self.assertEqual(first_turn['completed_at'], 11000)
        self.assertEqual(first_turn['status'], 'completed')
        self.assertEqual(len(self.load(recent_turns=1)['turns']), 1)
        self.assertEqual(self.load(recent_turns=0)['turns'], [])

    def test_structured_record_wins_for_usage_when_context_ledger_differs(self):
        self.register()
        first = usage(120, 30, cached=50, reasoning=15)
        next_call = usage(170, 30, cached=80, reasoning=5)
        prior_thread_usage = usage(1000, 20)
        complete_thread_usage = usage(1170, 50, cached=80, reasoning=5)
        first_record = record('t1', 'resp-1', first, first, prior_thread_usage)
        next_record = record('t2', 'resp-2', next_call, next_call, complete_thread_usage)
        wrong_owner = record('t2', 'resp-foreign', usage(9000, 1), next_call,
                             usage(9000, 1), thread=OTHER)
        displayed = usage(170, 30, cached=80, reasoning=5, total=222)
        self.append(meta(), event('task_started', turn_id='t1'), line('token_usage_record', **first_record),
                    count(first, first), event('task_complete', turn_id='t1'),
                    event('task_started', turn_id='t2'), line('token_usage_record', **next_record),
                    count(displayed, usage(290, 60)), line('token_usage_record', **wrong_owner),
                    line('token_usage_record', **first_record),
                    line('compacted', latest_token_usage_record=next_record),
                    count(displayed, usage(290, 60)))
        result = self.load()
        self.assertEqual(result['context_used'], 222)
        self.assertEqual(result['latest']['total_tokens'], 200)
        self.assertEqual(result['total']['total_tokens'], 1220)
        self.assertEqual(result['turns'][0]['usage']['total_tokens'], 200)
        self.assertEqual(result['turns'][1]['usage']['total_tokens'], 150)
        self.assertEqual(self.load(), result)

    def test_structured_per_turn_totals_are_snapshots_and_aborted_turns_remain_visible(self):
        self.register()
        first, last, total = usage(100, 10), usage(120, 5), usage(220, 15)
        self.append(meta(), line('turn_context', turn_id='t1', model='test-model'),
                    line('token_usage_record', **record('t1', 'resp-1', first, first, first)),
                    line('token_usage_record', **record('t1', 'resp-2', last, total, total)),
                    event('turn_aborted', aborted_at=55))
        result = self.load()
        self.assertIsNone(result['active_turn_id'])
        turn = result['turns'][0]
        self.assertEqual((turn['status'], turn['completed_at'], turn['usage']['total_tokens']), ('aborted', 55000, 235))
        self.assertEqual(turn['started_at'], 1795399381250)
        self.assertEqual(turn['calls'], 2)
        self.assertEqual(result['context_used'], 125)
        self.assertIsNone(result['context_window'])

    def test_compaction_checkpoint_restores_partial_turn_without_guessing_call_count(self):
        self.register()
        prior, last = usage(100, 10), usage(20, 5)
        prior_turn, full_turn = usage(200, 22), usage(220, 27)
        self.append(meta(history_base={'thread_id': THREAD}), event('task_started', turn_id='resume'),
                    line('compacted', latest_token_usage_record=record('resume', 'r1', prior, prior_turn, prior_turn)),
                    line('token_usage_record', **record('resume', 'r2', last, full_turn, full_turn)),
                    event('task_complete', turn_id='resume', error={'message': PRIVATE}))
        result = self.load()
        turn = result['turns'][0]
        self.assertEqual((turn['status'], turn['usage']['total_tokens'], turn['calls']), ('failed', 247, None))
        self.assertIsNone(result['active_turn_id'])
        self.assertNotIn(PRIVATE, json.dumps(result))

    def test_compaction_context_estimate_does_not_charge_usage_and_next_response_can_shrink_context(self):
        self.register()
        first, after = usage(1000, 20), usage(80, 10)
        self.append(meta(), event('task_started', turn_id='t'), count(first, first),
                    count(usage(0, 0, total=1200), usage(0, 0, total=2000)))
        snapshot = self.load()
        self.assertEqual(snapshot['context_used'], 1200)
        self.assertEqual(snapshot['total']['total_tokens'], 1020)
        self.assertEqual(snapshot['latest']['total_tokens'], 1020)
        self.append(count(after, usage(1080, 30)))
        snapshot = self.load()
        self.assertEqual(snapshot['context_used'], 90)
        self.assertEqual(snapshot['turns'][0]['usage']['total_tokens'], 1110)

    def test_rotated_path_rebuilds_and_keeps_stable_id_with_native_cumulative_checkpoint(self):
        self.register()
        first = usage(150, 20)
        self.append(meta(), event('task_started', turn_id='old'), count(first, first))
        before = self.load()
        self.assertEqual(before['total']['total_tokens'], 170)
        rotated = self.path(THREAD, ROTATED)
        recent = usage(50, 10)
        rotation_total = usage(380, 30)
        self.append(meta(history_base={'thread_id': THREAD, 'end_byte_offset': 1000}),
                    event('task_started', turn_id='current'),
                    line('token_usage_record', **record('current', 'resp-5', recent, recent, rotation_total)),
                    count(recent, recent), path=rotated)
        self.register(rotated)
        result = self.load()
        self.assertEqual(result['session']['id'], THREAD)
        self.assertEqual(result['session']['innerId'], ROTATED)
        self.assertEqual(self.adapter.list_recent_sessions()[0]['innerId'], ROTATED)
        self.assertEqual(result['total']['total_tokens'], 410)
        self.assertEqual(result['context_used'], 60)
        self.assertEqual([t['id'] for t in result['turns']], ['current'])
        self.assertFalse(result['history_complete'])
        self.assertIsNone(self.load(ROTATED))

    def test_same_path_file_replacement_and_truncate_then_regrow_rebuild(self):
        self.register()
        self.append(meta(), event('task_started', turn_id='old'), line('response_item', text='x' * 500))
        self.assertEqual(self.load()['active_turn_id'], 'old')
        replacement = self.folder / 'replacement.jsonl'
        replacement.write_bytes(meta() + event('task_started', turn_id='replaced'))
        replacement.replace(self.rollout)
        self.assertEqual([t['id'] for t in self.load()['turns']], ['replaced'])
        self.rollout.write_bytes(meta() + event('task_started', turn_id='rewritten')
                                 + line('response_item', text='x' * 1000))
        self.assertEqual([t['id'] for t in self.load()['turns']], ['rewritten'])
        self.rollout.write_bytes(meta())
        self.assertEqual(self.load()['turns'], [])

    def test_reader_limits_work_and_resumes_partial_line_without_reprocessing_prefix(self):
        self.register()
        start = meta() + event('task_started', turn_id='t')
        initial = usage(20, 5)
        completed = count(initial, initial)
        self.append(start, completed[:110])
        with patch.object(session_usage, '_READ_BYTES', 87):
            self.assertEqual(self.load()['status'], 'loading')
            for _ in range(10):
                partial = self.load()
                if partial['status'] == 'ready':
                    break
            self.assertEqual(partial['status'], 'ready')
        self.assertIsNone(self.load()['latest'])
        with patch.object(session_usage._Rollout, 'apply', autospec=True,
                          side_effect=session_usage._Rollout.apply) as apply:
            self.append(completed[110:])
            self.assertEqual(self.load()['latest']['total_tokens'], 25)
            self.assertEqual(self.load()['total']['total_tokens'], 25)
            self.assertEqual(apply.call_count, 1)

    def test_oversized_line_and_corrupt_usage_can_be_skipped_across_reads(self):
        self.register()
        sample = usage(2, 1)
        self.append(meta(), line('response_item', text='x' * 12000), b'{"type":"token_count",bad}\n',
                    b'{"type":"event_msg","payload":{"type":[],"turn_id":"t"}}\n',
                    event('task_started', turn_id='t'), count(sample, sample))
        with patch.object(session_usage, '_READ_BYTES', 350), patch.object(session_usage, '_LINE_BYTES', 700):
            for _ in range(50):
                observed = self.load()
                if observed['status'] == 'ready':
                    break
        self.assertEqual(observed['status'], 'ready')
        self.assertEqual(observed['context_used'], 3)
        self.assertEqual(observed['active_turn_id'], 't')
        self.assertFalse(observed['history_complete'])

    def test_missing_breakdown_does_not_invent_zero_and_usage_stays_json_serializable(self):
        self.register()
        partial = dict(total_tokens=12, input_tokens=10, cached_input_tokens=True,
                       reasoning_output_tokens=-1, output_tokens=2)
        self.append(meta(), event('task_started', turn_id='partial'), count(partial, partial))
        latest = self.load()['latest']
        self.assertEqual(latest['total_tokens'], 12)
        self.assertIsNone(latest['cached_input_tokens'])
        self.assertIsNone(latest['reasoning_output_tokens'])
        self.assertIsNone(latest['uncached_input_tokens'])
        self.assertIsNone(latest['non_reasoning_output_tokens'])
        self.assertEqual(json.loads(json.dumps(latest)), latest)

    def test_offline_index_updates_incrementally_and_lookup_repeats_without_database_row(self):
        self.append(meta())
        self.index.write_bytes(index_line(THREAD, 'First label'))
        self.assertEqual(self.adapter.list_recent_sessions()[0]['title'], 'First label')
        self.assertEqual(self.load()['session']['title'], 'First label')
        self.assertEqual(self.load()['session']['title'], 'First label')
        next_line = index_line(THREAD, 'Second label')
        with self.index.open('ab') as stream:
            stream.write(next_line[:30])
        self.assertEqual(self.load()['session']['title'], 'First label')
        with self.index.open('ab') as stream:
            stream.write(next_line[30:])
        self.assertEqual(self.load()['session']['title'], 'Second label')
        self.index.write_bytes(index_line(THREAD, 'Fresh index'))
        self.assertEqual(self.load()['session']['title'], 'Fresh index')
        self.assertEqual(self.load()['status'], 'ready')

    def test_database_reads_live_wal_and_creates_no_codex_files(self):
        self.append(meta())
        with closing(sqlite3.connect(self.db)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            self.register(name='WAL only')
            before = {p.name for p in self.home.iterdir()}
            self.assertEqual(self.adapter.list_recent_sessions()[0]['title'], 'WAL only')
            self.assertEqual(self.load()['status'], 'ready')
            self.assertEqual({p.name for p in self.home.iterdir()}, before)
        before = {p.name for p in self.home.iterdir()}
        self.assertEqual(self.load()['status'], 'ready')
        self.assertEqual({p.name for p in self.home.iterdir()}, before)

    def test_missing_or_mismatched_rollout_reports_unavailable_and_recovers(self):
        self.register()
        self.assertEqual(self.load()['status'], 'unavailable')
        self.append(meta(OTHER), event('task_started', turn_id='foreign'),
                    count(usage(1, 1), usage(1, 1)))
        self.assertEqual(self.load()['status'], 'unavailable')
        self.assertEqual(self.load()['turns'], [])
        self.rollout.write_bytes(meta() + event('task_started', turn_id='mine'))
        self.assertEqual(self.load()['active_turn_id'], 'mine')
        self.rollout.unlink()
        stale = self.load()
        self.assertEqual(stale['status'], 'unavailable')
        self.assertFalse(stale['history_complete'])

    def test_fallback_resolves_rotation_and_rejects_paths_outside_codex_stores(self):
        self.append(meta())
        self.assertEqual(self.load()['session']['innerId'], THREAD)
        rotated = self.path(THREAD, ROTATED)
        self.append(meta(), event('task_started', turn_id='rotated'), path=rotated)
        timestamp = self.rollout.stat().st_mtime_ns + 1_000_000
        os.utime(rotated, ns=(timestamp, timestamp))
        self.assertEqual(self.load()['session']['innerId'], ROTATED)
        outside = self.home / 'private.jsonl'
        outside.write_bytes(meta(OTHER))
        self.register(outside, OTHER, recency=10000)
        self.assertIsNone(self.load(OTHER))
        self.assertNotIn(OTHER, [entry['id'] for entry in self.adapter.list_recent_sessions()])

    def test_foreign_metadata_segment_cannot_attribute_turns_or_legacy_usage(self):
        self.register()
        legitimate = usage(25, 5)
        foreign = usage(250, 5)
        self.append(meta(), event('task_started', turn_id='mine'), count(legitimate, legitimate),
                    event('task_complete', turn_id='mine'), meta(OTHER),
                    event('task_started', turn_id='not-mine'), count(foreign, foreign))
        snapshot = self.load()
        self.assertEqual(snapshot['total']['total_tokens'], 30)
        self.assertEqual([turn['id'] for turn in snapshot['turns']], ['mine'])
        self.assertIsNone(snapshot['active_turn_id'])

    def test_history_bounds_and_returned_snapshots_are_safe_to_mutate(self):
        self.register()
        self.append(meta(), *(event('task_started', turn_id=f't{i}') + event('task_complete', turn_id=f't{i}')
                              for i in range(110)))
        returned = self.load(recent_turns=500)
        self.assertEqual(len(returned['turns']), 100)
        self.assertEqual((returned['turns'][0]['id'], returned['turns'][-1]['id']), ('t109', 't10'))
        returned['turns'][0]['id'] = 'changed'
        returned['session']['id'] = 'changed'
        fresh = self.load()
        self.assertEqual(fresh['session']['id'], THREAD)
        self.assertEqual(fresh['turns'][0]['id'], 't109')


if __name__ == '__main__':
    unittest.main()
