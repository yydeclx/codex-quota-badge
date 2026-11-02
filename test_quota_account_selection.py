"""Offline role selection: no user credentials, network, or running gateway."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import quota_badge as quota


class QuotaAccountSelectionTests(unittest.TestCase):
    NOW = 1_800_000_000

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.root = self.home / '.opencodex'
        self.root.mkdir()
        for target, value in (('quota_badge.Path.home', self.home),
                              ('quota_badge.time.time', self.NOW)):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ('_provider_quota_output', '_fetch_legacy_quota', 'owned_process'):
            patcher = patch.object(quota, name, side_effect=AssertionError('unscoped read'))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.config = {'codexAccounts': [
            {'id': 'secondary-fixture-A', 'isMain': False, 'plan': 'team',
             'email': 'DO-NOT-PUBLISH', 'token': 'DO-NOT-PUBLISH'}]}
        self.cache = {'quotas': {
            '__main__': self.observation(37),
            'secondary-fixture-A': self.observation(50)},
            'mainPolicyQuota': self.observation(99)}
        self.save()

    def observation(self, used):
        return {'weeklyPercent': used, 'weeklyResetAt': self.NOW + 86400,
                'updatedAt': (self.NOW - 120) * 1000}

    def save(self):
        for name, data in (('config.json', self.config),
                           ('codex-quota-cache.json', self.cache)):
            (self.root / name).write_text(json.dumps(data), encoding='utf-8')

    def test_default_is_main_even_when_current_cli_would_be_secondary(self):
        self.assertEqual(quota.fetch_quota(), quota.fetch_quota('main'))
        main, secondary = quota.fetch_quota(), quota.fetch_quota('secondary')
        self.assertEqual((main['remaining'], secondary['remaining']), (63, 50))
        self.assertEqual((main['quotaAccount'], secondary['quotaAccount']), ('main', 'secondary'))
        self.assertNotEqual(main['quotaAccountKey'], secondary['quotaAccountKey'])
        self.assertEqual(secondary['planType'], 'team')
        self.assertIsNone(main['planType'])
        self.assertEqual(main['quotaScope'], 'selected-account')

    def test_switch_back_keeps_history_key_and_does_not_refresh_observation(self):
        first = quota.fetch_quota()
        quota.fetch_quota('secondary')
        with patch('quota_badge.time.time', return_value=self.NOW + 300):
            again = quota.fetch_quota()
        self.assertEqual(first, again)
        self.assertEqual(again['updatedAt'], self.NOW - 120)

    def test_fingerprint_changes_with_secondary_identity_not_usage_or_plan(self):
        first = quota.fetch_quota('secondary')['quotaAccountKey']
        self.cache['quotas']['secondary-fixture-A']['weeklyPercent'] = 60
        self.config['codexAccounts'][0]['plan'] = 'plus'
        self.save()
        self.assertEqual(quota.fetch_quota('secondary')['quotaAccountKey'], first)
        self.config['codexAccounts'][0]['id'] = 'secondary-fixture-B'
        self.cache['quotas']['secondary-fixture-B'] = self.observation(20)
        self.save()
        self.assertNotEqual(quota.fetch_quota('secondary')['quotaAccountKey'], first)

    def test_result_does_not_publish_config_identity_or_private_fields(self):
        result = quota.fetch_quota('secondary')
        text = json.dumps(result)
        self.assertNotIn('secondary-fixture-A', text)
        self.assertNotIn('DO-NOT-PUBLISH', text)
        self.assertRegex(result['quotaAccountKey'], r'^[0-9a-f]{64}$')
        self.config['codexAccounts'][0]['plan'] = 'DO-NOT-PUBLISH'
        self.save()
        self.assertIsNone(quota.fetch_quota('secondary')['planType'])

    def test_main_works_without_config_or_main_identity(self):
        (self.root / 'config.json').unlink()
        self.assertEqual(quota.fetch_quota()['remaining'], 63)
        with self.assertRaises(ValueError):
            quota.fetch_quota('secondary')

    def test_missing_selected_cache_never_uses_other_account_or_policy(self):
        for role, selected_id in (('main', '__main__'), ('secondary', 'secondary-fixture-A')):
            with self.subTest(role=role):
                original = self.cache['quotas'].pop(selected_id)
                self.save()
                with self.assertRaisesRegex(ValueError, 'Selected account quota unavailable'):
                    quota.fetch_quota(role)
                self.cache['quotas'][selected_id] = original

    def test_unconfigured_old_secondary_cache_cannot_be_selected(self):
        self.config['codexAccounts'] = []
        self.save()
        with self.assertRaises(ValueError):
            quota.fetch_quota('secondary')
        self.assertEqual(quota.fetch_quota()['remaining'], 63)

    def test_multiple_secondaries_are_ambiguous_even_if_only_one_has_cache(self):
        self.config['codexAccounts'].append({'id': 'other-secondary', 'isMain': False})
        self.save()
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            quota.fetch_quota('secondary')

    def test_main_config_row_does_not_count_as_secondary(self):
        self.config['codexAccounts'].insert(0, {'id': '__main__', 'isMain': True})
        self.save()
        self.assertEqual(quota.fetch_quota('secondary')['remaining'], 50)

    def test_secondary_role_must_be_explicit_and_not_reserved(self):
        original = copy.deepcopy(self.config)
        for change in ({'isMain': None}, {'isMain': 0}, {'id': '__main__'},
                       {'id': '__pool__'}, {'id': ''}, {'id': 123}):
            with self.subTest(change=change):
                self.config = copy.deepcopy(original)
                self.config['codexAccounts'][0].update(change)
                self.save()
                with self.assertRaises(ValueError):
                    quota.fetch_quota('secondary')

    def test_only_exact_role_values_are_accepted_before_any_read(self):
        for value in (' secondary', 'SECONDARY', '', 'current', 'pool', None, [], True):
            with self.subTest(value=value), patch.object(quota, '_quota_cache_json') as read:
                with self.assertRaises(ValueError):
                    quota.fetch_quota(value)
                read.assert_not_called()

    def test_staleness_is_bounded_and_rereading_cannot_renew_it(self):
        self.assertLessEqual(quota.QUOTA_CACHE_MAX_AGE, 15 * 60)
        self.cache['quotas']['__main__']['updatedAt'] = self.NOW - quota.QUOTA_CACHE_MAX_AGE
        self.save()
        self.assertEqual(quota.fetch_quota()['updatedAt'], self.NOW - quota.QUOTA_CACHE_MAX_AGE)
        with patch('quota_badge.time.time', return_value=self.NOW + 1):
            with self.assertRaisesRegex(ValueError, 'stale'):
                quota.fetch_quota()
            self.assertEqual(quota.fetch_quota('secondary')['remaining'], 50)

    def test_missing_invalid_and_future_observation_times_are_rejected(self):
        for stamp in (None, True, 'recent', 0, float('nan'), float('inf'), self.NOW + 1):
            with self.subTest(stamp=stamp):
                self.cache['quotas']['__main__']['updatedAt'] = stamp
                self.save()
                with self.assertRaises(ValueError):
                    quota.fetch_quota()

    def test_percent_and_reset_must_be_complete_in_the_selected_row(self):
        original = self.observation(37)
        for field, values in (
                ('weeklyPercent', (None, True, -1, 101, float('nan'), '37')),
                ('weeklyResetAt', (None, True, self.NOW, self.NOW - 1, float('inf')))):
            for value in values:
                with self.subTest(field=field, value=value):
                    self.cache['quotas']['__main__'] = {**original, field: value}
                    self.save()
                    with self.assertRaises(ValueError):
                        quota.fetch_quota()

    def test_zero_and_full_use_are_valid_and_ms_resets_are_normalized(self):
        for used, remaining in ((0, 100), (100, 0)):
            self.cache['quotas']['__main__'].update(
                weeklyPercent=used, weeklyResetAt=(self.NOW + 86400) * 1000)
            self.save()
            result = quota.fetch_quota()
            self.assertEqual(result['remaining'], remaining)
            self.assertEqual(result['resetsAt'], self.NOW + 86400)

    def test_optional_five_hour_never_borrows_another_accounts_window(self):
        self.cache['quotas']['secondary-fixture-A'].update(
            fiveHourPercent=80, fiveHourResetAt=self.NOW + 3600)
        self.save()
        self.assertIsNone(quota.fetch_quota()['fiveHour'])
        self.assertEqual(quota.fetch_quota('secondary')['fiveHour']['remaining'], 20)

    def test_short_window_requires_known_duration_and_its_own_fresh_observation(self):
        row = self.cache['quotas']['secondary-fixture-A']
        row.update(shortPercent=25, shortResetAt=self.NOW + 3600,
                   shortObservedAt=(self.NOW - 60) * 1000, shortWindowSeconds=18000)
        self.save()
        self.assertEqual(quota.fetch_quota('secondary')['fiveHour']['remaining'], 75)
        for changes in ({'shortWindowSeconds': 3600}, {'shortObservedAt': None},
                        {'shortObservedAt': self.NOW - quota.QUOTA_CACHE_MAX_AGE - 1},
                        {'shortResetAt': self.NOW - 1}):
            with self.subTest(changes=changes):
                original = dict(row)
                row.update(changes)
                self.save()
                self.assertIsNone(quota.fetch_quota('secondary')['fiveHour'])
                row.clear()
                row.update(original)

    def test_bad_json_and_oversized_files_fail_without_private_exception_text(self):
        path = self.root / 'codex-quota-cache.json'
        for raw in ('{"DO-NOT-PUBLISH"', '[]', '{"quotas": []}',
                    'x' * (quota.MAX_QUOTA_OUTPUT + 1)):
            with self.subTest(size=len(raw)):
                path.write_text(raw, encoding='utf-8')
                with self.assertRaises(ValueError) as caught:
                    quota.fetch_quota()
                self.assertNotIn('DO-NOT-PUBLISH', str(caught.exception))

    def test_only_allowlisted_cache_and_config_files_are_read_without_writes(self):
        original_open = Path.open
        reads = []

        def tracked_open(path, *args, **kwargs):
            reads.append((path.name, args[0] if args else kwargs.get('mode', 'r')))
            return original_open(path, *args, **kwargs)

        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        with patch.object(Path, 'open', tracked_open):
            quota.fetch_quota()
            quota.fetch_quota('secondary')
        self.assertEqual(reads, [('codex-quota-cache.json', 'rb'),
                                ('config.json', 'rb'), ('codex-quota-cache.json', 'rb')])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})


if __name__ == '__main__':
    unittest.main()
