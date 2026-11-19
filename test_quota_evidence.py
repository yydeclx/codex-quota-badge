"""Read-only evidence regressions, using synthetic logs in temporary directories."""

import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import quota_evidence as evidence


BASE = 1_790_000_100  # Divisible by 300, in epoch seconds.
ACCOUNTS = {'__main__', 'secondary-cache-id'}


def entry(name='request-1', at=BASE, provider='openai', **extra):
    row = {'requestId': name, 'timestamp': at * 1000, 'provider': provider,
           'status': 200, 'usage': {'inputTokens': 100, 'cachedInputTokens': 80,
                                    'outputTokens': 10, 'totalTokens': 110}}
    row.update(extra)
    return row


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        evidence._CACHES.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.path = self.home / 'usage.jsonl'

    def write(self, *rows, append=False):
        with self.path.open('ab' if append else 'wb') as stream:
            for row in rows:
                stream.write(json.dumps(row).encode() + b'\n')

    def collect(self, since=BASE, until=BASE + 3600, accounts=ACCOUNTS):
        return evidence.collect_requests(self.home, since, until, accounts)

    def total(self, result):
        return sum(row['weight'] for row in result['requests'])

    def test_weight_bucket_and_actual_extrema(self):
        self.write(entry('a', BASE), entry('b', BASE + 20), entry('c', BASE + 301))
        result = self.collect()
        self.assertTrue(result['complete'])
        self.assertEqual(result['start'], BASE)
        self.assertEqual(result['end'], BASE + 3600)
        self.assertEqual(result['method'], 'usage-log')
        self.assertEqual(result['requests'], [
            {'at': BASE, 'start': BASE, 'end': BASE + 20, 'weight': 76, 'account': None},
            {'at': BASE + 301, 'start': BASE + 301, 'end': BASE + 301, 'weight': 38, 'account': None}])

    def test_details_missing_use_total_and_cache_read_alias(self):
        self.write(entry('a', usage={'inputTokens': 100, 'outputTokens': 10, 'totalTokens': 110}),
                   entry('b', usage=None, totalTokens=15),
                   entry('c', usage={'inputTokens': 100, 'outputTokens': 10, 'cacheReadInputTokens': 80}))
        self.assertEqual(self.total(self.collect()), 163)

    def test_duplicate_append_and_attempts_count_once(self):
        row = entry(attempts=[{'usage': {'totalTokens': 9999}}] * 3)
        self.write(row, row)
        self.assertEqual(self.total(self.collect()), 38)
        self.write(row, append=True)
        self.assertEqual(self.total(self.collect()), 38)
        self.write(entry(usage={'totalTokens': 80}), append=True)
        self.assertEqual(self.total(self.collect()), 80)
        cache = next(iter(evidence._CACHES.values()))
        self.assertTrue(all(isinstance(key, bytes) and len(key) == 32 for key in cache.records))
        self.assertNotIn('request-1', repr(cache))

    def test_logical_request_attempt_rows_count_once(self):
        self.write(entry('attempt-1', logicalRequestId='logical', usage={'totalTokens': 80}),
                   entry('attempt-2', logicalRequestId='logical', usage={'totalTokens': 100}))
        self.assertEqual(self.total(self.collect()), 100)

    def test_request_id_dedup_survives_later_logical_id(self):
        self.write(entry('same-id', usage={'totalTokens': 80}))
        self.assertEqual(self.total(self.collect()), 80)
        self.write(entry('same-id', logicalRequestId='logical', usage={'totalTokens': 100}),
                   entry('another-attempt', logicalRequestId='logical', usage={'totalTokens': 90}), append=True)
        self.assertEqual(self.total(self.collect()), 100)

    def test_failure_with_measured_usage_counts_zero_or_unbilled_does_not(self):
        self.write(entry('failed-billed', status=500), entry('failed-empty', status=500, usage=None),
                   entry('failed-zero', status=429, usage={'totalTokens': 0}),
                   entry('successful-zero', usage={'totalTokens': 0}))
        result = self.collect()
        self.assertTrue(result['complete'])
        self.assertEqual(self.total(result), 38)

    def test_real_shape_unreported_502_null_usage_is_not_damage(self):
        self.write(*(entry(str(i), BASE + i, status=502, usage=None,
                           totalTokens=None, usageStatus='unreported', durationMs=1000)
                     for i in range(20)))
        result = self.collect(BASE + 1)
        self.assertTrue(result['complete'])
        self.assertTrue(result['coverageComplete'])
        self.assertEqual(result['requests'], [])
        self.assertNotIn('error', result)
        self.write(entry('paid-failure', BASE + 30, status=502, totalTokens=None), append=True)
        self.assertEqual(self.total(self.collect(BASE + 1)), 38)

    def test_success_missing_usage_is_local_gap_and_recent_suffix_recovers(self):
        self.write(entry('old', BASE), entry('missing', BASE + 10, usage=None,
                                            totalTokens=None, usageStatus='unreported'))
        missing = self.collect(BASE, BASE + 15)
        self.assertFalse(missing['complete'])
        self.assertFalse(missing['coverageComplete'])
        self.assertEqual(missing['error'], 'missing-usage')
        self.write(entry('recent', BASE + 20), append=True)
        whole = self.collect(BASE, BASE + 100)
        self.assertFalse(whole['complete'])
        self.assertTrue(whole['coverageComplete'])
        self.assertEqual(whole['start'], BASE + 20)
        spans=whole['coverageRanges']
        self.assertTrue(any(lo<=BASE and BASE+5<=hi for lo,hi in spans))
        self.assertFalse(any(lo<=BASE+10<=hi for lo,hi in spans))
        with patch.object(Path, 'open', side_effect=AssertionError('reparsed')):
            recent = self.collect(BASE + 20, BASE + 100)
            before_gap = self.collect(BASE, BASE + 5)
        self.assertTrue(recent['complete'])
        self.assertTrue(recent['coverageComplete'])
        self.assertNotIn('error', recent)
        self.assertTrue(before_gap['complete'])

    def test_old_bad_tokens_do_not_poison_disjoint_recent_window(self):
        self.write(entry('bad', BASE, usage={'totalTokens': True}), entry('good', BASE + 30))
        whole = self.collect()
        self.assertFalse(whole['complete'])
        self.assertTrue(whole['coverageComplete'])
        self.assertEqual(whole['start'], BASE + 30)
        self.assertTrue(self.collect(BASE + 30)['complete'])
        self.assertFalse(self.collect(BASE, BASE + 10)['coverageComplete'])

    def test_old_malformed_line_recovers_with_bounded_time_evidence(self):
        self.write(entry('before', BASE))
        with self.path.open('ab') as stream:
            stream.write(b'{malformed}\n')
        self.write(entry('after', BASE + 20), entry('recent', BASE + 40), append=True)
        self.assertFalse(self.collect()['complete'])
        recent = self.collect(BASE + 40)
        self.assertTrue(recent['complete'])
        self.assertTrue(recent['coverageComplete'])
        self.assertNotIn('error', recent)

    def test_consumption_at_completion_controls_window_bucket_and_extrema(self):
        # timestamp is request START. Both these requests consume in the next
        # 5-minute bucket; metadata and window filtering must use completion.
        self.write(entry('coverage', BASE, usage={'totalTokens': 0}),
                   entry('long', BASE + 290, durationMs=20_000),
                   entry('short', BASE + 300, durationMs=5_000))
        full = self.collect(BASE, BASE + 320)
        self.assertTrue(full['complete'])
        self.assertEqual(full['requests'], [{'at': BASE + 305, 'start': BASE + 305,
                                             'end': BASE + 310, 'weight': 76, 'account': None}])
        self.assertEqual(self.collect(BASE, BASE + 300)['requests'], [])
        self.assertEqual(self.total(self.collect(BASE + 300, BASE + 309)), 38)
        self.assertEqual(self.collect(BASE + 310, BASE + 310)['requests'][0]['at'], BASE + 310)

    def test_duplicate_completion_updates_at_without_adding_work(self):
        self.write(entry('same', BASE, durationMs=1_000))
        self.collect()
        self.write(entry('same', BASE, durationMs=2_000), append=True)
        result = self.collect()
        self.assertEqual(self.total(result), 38)
        self.assertEqual(result['requests'][0]['at'], BASE + 2)

    def test_invalid_duration_is_not_silently_used_as_start_time(self):
        for duration in (True, math.nan, math.inf, -1, '1000'):
            with self.subTest(duration=duration):
                evidence._CACHES.clear()
                self.write(entry(durationMs=duration))
                result = self.collect()
                self.assertFalse(result['coverageComplete'])
                self.assertEqual(result['requests'], [])

    def test_third_party_and_combo_never_count(self):
        providers = ['SKG', 'SKG2', 'OCX', 'combo', 'combo/openai', 'skg-main',
                     'openai-custom', 'openai-SKG', 'OpenAI', 'openai-k' + 'a' * 32]
        rows = [entry(str(i), provider=provider, actualAccountId='__main__')
                for i, provider in enumerate(providers)]
        rows += [entry('combo-child', routeDecision={'routeKind': 'combo-fallback'}),
                 entry('third-model', requestedModel='SKG/gpt'),
                 entry('third-model-2', resolvedModel='ocx/gpt'),
                 entry('combo-model', model='combo/test')]
        self.write(*rows)
        result = self.collect()
        self.assertTrue(result['complete'])
        self.assertEqual(result['requests'], [])

    def test_bare_official_provider_does_not_identify_an_account(self):
        self.write(*(entry(p, provider=p, currentAccount='__main__', accountId='secondary-cache-id',
                           routeDecision={'selected': {'accountId': 'secondary-cache-id'}})
                     for p in ('openai', 'chatgpt', 'openai-multi', 'openai-pabcdef')))
        result = self.collect()
        self.assertEqual(self.total(result), 152)
        self.assertEqual(result['requests'][0]['account'], None)

    def test_only_documented_main_labels(self):
        self.write(*(entry(p, provider=p) for p in ('openai-main', 'chatgpt-main', 'openai-multi-main')))
        self.assertEqual(self.collect()['requests'][0]['account'], '__main__')
        self.assertEqual(self.collect(accounts={'secondary-cache-id'})['requests'][0]['account'], None)

    def test_explicit_account_fields_whitelist_and_conflicts(self):
        self.write(entry('a', actualAccountId='secondary-cache-id'),
                   entry('b', codexAccountId='secondary-cache-id'),
                   entry('c', billingAccountId='__main__'),
                   entry('d', billingAccountId='unknown-id'),
                   entry('e', actualAccountId='__main__', codexAccountId='secondary-cache-id'),
                   entry('f', provider='openai-main', actualAccountId='secondary-cache-id'))
        by_account = {r['account']: r['weight'] for r in self.collect()['requests']}
        self.assertEqual(by_account, {'secondary-cache-id': 76, '__main__': 38, None: 114})

    def test_changing_windows_and_accounts_reuses_all_evidence(self):
        self.write(entry('a', BASE, actualAccountId='__main__'),
                   entry('b', BASE + 20, billingAccountId='secondary-cache-id'),
                   entry('c', BASE + 400, actualAccountId='__main__'))
        first = self.collect(BASE + 10, BASE + 30, {'__main__'})
        self.assertEqual(self.total(first), 38)
        self.assertIsNone(first['requests'][0]['account'])
        with patch.object(evidence, '_ingest', side_effect=AssertionError('reparsed')):
            second = self.collect(BASE, BASE + 500, ACCOUNTS)
            boundary = self.collect(BASE + 5, BASE + 30)
            narrow = self.collect(BASE + 400, BASE + 400)
        self.assertEqual(self.total(second), 114)
        self.assertEqual(boundary['requests'][0]['account'], 'secondary-cache-id')
        self.assertEqual(boundary['requests'][0]['start'], BASE + 20)
        self.assertEqual(self.total(narrow), 38)

    def test_same_file_fast_path_no_open_or_parse_and_result_is_detached(self):
        self.write(entry())
        first = self.collect()
        first['requests'][0]['weight'] = 12345
        with patch.object(Path, 'open', side_effect=AssertionError('opened unchanged file')):
            second = self.collect()
        self.assertEqual(self.total(second), 38)

    def test_append_only_parses_added_line_even_with_same_mtime(self):
        self.write(entry())
        self.collect()
        before = self.path.stat()
        self.write(entry('new', BASE + 10), append=True)
        os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with patch.object(evidence, '_ingest', wraps=evidence._ingest) as ingest:
            result = self.collect()
        self.assertEqual(ingest.call_count, 1)
        self.assertEqual(self.total(result), 76)

    def test_partial_line_waits_for_append_without_reparsing(self):
        self.write(entry('a'))
        raw = json.dumps(entry('b', BASE + 20)).encode()
        with self.path.open('ab') as stream:
            stream.write(raw[:50])
        partial = self.collect()
        self.assertFalse(partial['complete'])
        self.assertEqual(partial['error'], 'partial-line')
        self.assertEqual(self.total(partial), 38)
        with patch.object(Path, 'open', side_effect=AssertionError('reopened partial')):
            self.assertEqual(self.collect(), partial)
        with self.path.open('ab') as stream:
            stream.write(raw[50:] + b'\n')
        self.assertTrue(self.collect()['complete'])
        self.assertEqual(self.total(self.collect()), 76)

    def test_truncate_and_rotation_discard_previous_generation(self):
        self.write(entry('a'), entry('b'))
        self.assertEqual(self.total(self.collect()), 76)
        self.write(entry('c', BASE + 20))
        truncated = self.collect()
        self.assertEqual(self.total(truncated), 38)
        self.assertFalse(truncated['complete'])
        self.assertEqual(truncated['start'], BASE + 20)
        self.path.rename(self.home / 'usage.old')
        self.write(entry('rotated', BASE - 10))
        rotated = self.collect(BASE - 10)
        self.assertTrue(rotated['complete'])
        self.assertEqual(self.total(rotated), 38)

    def test_rotation_recent_coverage_complete_while_week_is_incomplete(self):
        self.write(entry('old-generation', BASE - 7 * 86400))
        self.collect(BASE - 7 * 86400)
        self.path.rename(self.home / 'usage.old')
        self.write(entry('new-generation', BASE, durationMs=10_000))
        week = self.collect(BASE - 7 * 86400)
        self.assertFalse(week['complete'])
        self.assertTrue(week['coverageComplete'])
        self.assertEqual(week['start'], BASE + 10)
        self.assertEqual(week['error'], 'coverage-gap')
        recent = self.collect(BASE + 10)
        self.assertTrue(recent['complete'])
        self.assertTrue(recent['coverageComplete'])
        self.assertEqual(self.total(recent), 38)

    def test_truncate_regrow_detected_by_anchors(self):
        self.write(entry('original', BASE, usage={'totalTokens': 500}))
        self.collect()
        self.write(entry('replacement-a', BASE + 10), entry('replacement-b', BASE + 20))
        result = self.collect(BASE + 10)
        self.assertEqual(self.total(result), 76)
        self.assertEqual(result['start'], BASE + 10)

    def test_same_size_rewrite_with_new_mtime(self):
        self.write(entry('a', usage={'totalTokens': 10}))
        self.collect()
        stat = self.path.stat()
        self.write(entry('b', usage={'totalTokens': 20}))
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        self.assertEqual(self.total(self.collect()), 20)

    def test_tail_before_since_is_complete_despite_omitted_prefix(self):
        self.write(*(entry(str(i), BASE + i * 60) for i in range(30)))
        with patch.object(evidence, '_TAIL_BYTES', 1600):
            result = self.collect(BASE + 27 * 60, BASE + 30 * 60)
        self.assertTrue(result['complete'])
        self.assertLess(result['start'], BASE + 27 * 60)
        self.assertGreater(result['start'], BASE)
        self.assertEqual(self.total(result), 3 * 38)
        earlier = self.collect(BASE)
        self.assertFalse(earlier['complete'])
        self.assertEqual(earlier['error'], 'coverage-gap')

    def test_line_budget_resumes_without_repeating_prefix(self):
        self.write(*(entry(str(i), BASE + i) for i in range(5)))
        with patch.object(evidence, '_MAX_LINES', 2):
            first = self.collect()
            self.assertFalse(first['complete'])
            self.assertEqual(first['error'], 'resource-limit')
            self.assertEqual(self.total(first), 76)
            second = self.collect()
            self.assertFalse(second['complete'])
            third = self.collect()
        self.assertTrue(third['complete'])
        self.assertEqual(self.total(third), 190)

    def test_time_budget_returns_partial_then_resumes(self):
        self.write(entry())
        with patch.object(evidence, '_SECONDS', 0):
            result = self.collect()
        self.assertFalse(result['complete'])
        self.assertEqual(result['error'], 'resource-limit')
        self.assertTrue(self.collect()['complete'])

    def test_out_of_order_record_does_not_backdate_coverage(self):
        self.write(entry('first', BASE + 100), entry('late-old-row', BASE))
        result = self.collect()
        self.assertFalse(result['complete'])
        self.assertEqual(result['start'], BASE + 100)

    def test_read_limits_and_no_old_prefix_read_on_first_scan(self):
        # Audit actual reads, not just constants. Only this synthetic log is used.
        self.write(*(entry(str(i), BASE + i) for i in range(80)))
        original_open = Path.open
        reads = []

        class Reader:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def read(self, limit=-1):
                before = self.stream.tell()
                data = self.stream.read(limit)
                reads.append((before, len(data), 'read'))
                return data

            def readline(self, limit=-1):
                before = self.stream.tell()
                data = self.stream.readline(limit)
                reads.append((before, len(data), 'line'))
                return data

        def audited_open(path, mode='r', *args, **kwargs):
            self.assertEqual(mode, 'rb')
            return Reader(original_open(path, mode, *args, **kwargs))

        with patch.object(evidence, '_TAIL_BYTES', 4096), patch.object(evidence, '_LINE_BYTES', 256), \
                patch.object(Path, 'open', audited_open):
            result = self.collect(BASE + 79)
        self.assertTrue(result['complete'])
        self.assertLessEqual(sum(length for _, length, _ in reads), 4096)
        self.assertTrue(all(length <= 256 for _, length, kind in reads if kind == 'line'))
        self.assertGreaterEqual(min(offset for offset, _, _ in reads), self.path.stat().st_size - 4096)

    def test_oversized_line_is_not_parsed_and_remains_incomplete(self):
        with self.path.open('wb') as stream:
            stream.write(b'x' * (evidence._LINE_BYTES + 20) + b'\n')
        self.write(entry(), append=True)
        result = self.collect()
        self.assertFalse(result['complete'])
        self.assertEqual(result['error'], 'line-too-large')
        self.assertEqual(self.total(result), 38)

    def test_damaged_record_safe_error_no_sensitive_output(self):
        self.path.write_bytes(b'{"secret":"bearer do-not-output",bad}\n')
        self.write(entry(), append=True)
        result = self.collect()
        self.assertEqual(result['error'], 'invalid-record')
        self.assertFalse(result['complete'])
        serialized = json.dumps(result)
        self.assertNotIn('bearer', serialized)
        self.assertNotIn('request-1', serialized)
        self.assertNotIn(str(self.home), serialized)
        self.assertEqual(set(result['requests'][0]), {'at', 'weight', 'account', 'start', 'end'})

    def test_bad_timestamp_and_token_values(self):
        for value in (True, False, math.nan, math.inf, -1, '12', [], {}, 10 ** 400):
            with self.subTest(value=repr(value)[:30]):
                evidence._CACHES.clear()
                self.write(entry(usage={'inputTokens': value, 'totalTokens': 100}))
                result = self.collect()
                self.assertFalse(result['complete'])
                self.assertEqual(result['requests'], [])
                evidence._CACHES.clear()
                self.write(entry(timestamp=value))
                result = self.collect()
                self.assertFalse(result['complete'])
                self.assertIsNone(result['start'])

    def test_inconsistent_cached_and_overflow_are_rejected(self):
        self.write(entry('cached', usage={'inputTokens': 1, 'cachedInputTokens': 2, 'outputTokens': 0}),
                   entry('overflow', usage={'inputTokens': 1e308, 'cachedInputTokens': 0, 'outputTokens': 1e308}))
        result = self.collect()
        self.assertFalse(result['complete'])
        self.assertEqual(result['requests'], [])

    def test_aggregate_overflow_never_outputs_infinity(self):
        self.write(entry('a', usage={'totalTokens': 1e308}), entry('b', usage={'totalTokens': 1e308}))
        result = self.collect()
        self.assertFalse(result['complete'])
        json.dumps(result, allow_nan=False)

    def test_seconds_and_iso_timestamp(self):
        self.write(entry('a', timestamp=BASE), entry('b', timestamp='2026-09-21T14:15:00+00:00'))
        result = self.collect(0, BASE + 86400)
        self.assertEqual(len(next(iter(evidence._CACHES.values())).records), 2)
        self.assertTrue(all(r['at'] < 10_000_000_000 for r in result['requests']))

    def test_invalid_windows_and_accounts(self):
        self.write(entry())
        for start, end in ((True, BASE), (BASE, False), (math.nan, BASE), (BASE, math.inf), (2, 1)):
            result = self.collect(start, end)
            self.assertFalse(result['complete'])
            self.assertEqual(result['error'], 'invalid-window')
            json.dumps(result, allow_nan=False)
        self.assertEqual(self.collect(accounts='__main__')['error'], 'invalid-accounts')

    def test_missing_empty_log_and_no_home_writes(self):
        self.assertEqual(self.collect()['error'], 'unavailable')
        self.assertEqual(list(self.home.iterdir()), [])
        self.path.touch()
        result = self.collect()
        self.assertFalse(result['complete'])
        self.assertIsNone(result['start'])
        self.assertEqual(list(self.home.iterdir()), [self.path])

    def test_two_home_lru_bound(self):
        self.write(entry())
        self.collect()
        for i in range(3):
            home = self.home / str(i)
            home.mkdir()
            (home / 'usage.jsonl').write_text(json.dumps(entry(str(i))) + '\n')
            evidence.collect_requests(home, BASE, BASE + 3600, ACCOUNTS)
        self.assertEqual(len(evidence._CACHES), 2)

    def test_eight_day_retention_is_independent_of_query_until(self):
        self.write(entry('old', BASE), entry('recent', BASE + 9 * 86400))
        old = self.collect(BASE, BASE + 10)
        self.assertFalse(old['complete'])
        self.assertEqual(old['requests'], [])
        recent = self.collect(BASE + 9 * 86400, BASE + 10 * 86400)
        self.assertTrue(recent['complete'])
        self.assertEqual(self.total(recent), 38)
        self.collect(BASE + 90 * 86400, BASE + 91 * 86400)
        self.assertEqual(self.total(self.collect(BASE + 9 * 86400, BASE + 10 * 86400)), 38)

    def test_bucket_and_request_retention_bounds_expose_coverage_gap(self):
        self.write(*(entry(str(i), BASE + i * 300) for i in range(10)))
        with patch.object(evidence, '_MAX_BUCKETS', 4):
            result = self.collect(accounts=set())
        self.assertFalse(result['complete'])
        self.assertLessEqual(len(result['requests']), 4)
        self.assertEqual(result['start'], BASE + 6 * 300)
        evidence._CACHES.clear()
        self.write(*(entry(str(i), BASE + i) for i in range(10)))
        with patch.object(evidence, '_MAX_RECORDS', 4):
            result = self.collect()
        self.assertFalse(result['complete'])
        self.assertLessEqual(len(next(iter(evidence._CACHES.values())).records), 4)
        self.assertEqual(self.total(result), 4 * 38)

    def test_no_request_id_is_not_safe_to_count(self):
        row = entry()
        del row['requestId']
        self.write(row)
        result = self.collect()
        self.assertEqual(result['error'], 'invalid-record')
        self.assertEqual(result['requests'], [])


if __name__ == '__main__':
    unittest.main()
