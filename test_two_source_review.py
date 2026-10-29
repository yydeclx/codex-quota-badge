"""Independent review regressions using synthetic data and no persistence or UI."""
import copy
import datetime as dt
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import itertools
import unittest

from quota_accounting import quota_breakdown


def at(day, clock='12:00:00'):
    # The ledger uses local calendar dates; stay independent of the host offset.
    return dt.datetime.fromisoformat(day + 'T' + clock).timestamp()


def decimal(value):
    return Decimal(str(value))


def rounding_choices(value):
    units = decimal(value)
    return sorted({int(units.to_integral_value(rounding=ROUND_FLOOR)),
                   int(units.to_integral_value(rounding=ROUND_CEILING))})


def minimum_row_error(rows, local_target, other_target):
    """Small exhaustive oracle, independent of the production rounding DP."""
    options = [list(itertools.product(rounding_choices(row['local']),
                                      rounding_choices(row['other']))) for row in rows]
    best = None
    for candidate in itertools.product(*options):
        if sum(local for local, other in candidate) != local_target:
            continue
        if sum(other for local, other in candidate) != other_target:
            continue
        error = sum((abs(decimal(local + other) - decimal(row['total'] or 0))
                     for row, (local, other) in zip(rows, candidate)), Decimal(0))
        if best is None or error < best:
            best = error
    return best


class TwoSourceReviewTests(unittest.TestCase):
    def test_p1_cross_midnight_projection_uses_sample_time_without_mutating_input(self):
        previous_at = at('2026-10-28', '23:50:00')
        sample_at = at('2026-10-29', '00:10:00')
        reset = at('2026-11-04')
        source = {'reset': reset, 'firstAt': previous_at, 'firstRemaining': 100,
                  'lastAt': previous_at, 'lastRemaining': 100}
        original = copy.deepcopy(source)

        summary = quota_breakdown(source, 90, reset, '2026-10-29', sample_at=sample_at)

        self.assertEqual(source, original)
        self.assertEqual(summary['ledger']['lastAt'], sample_at)
        self.assertEqual(summary['ledger']['lastRemaining'], 90)
        self.assertEqual((summary['total'], summary['local'], summary['other']), (10, 5, 5))
        nonzero = {row['day'].isoformat(): (row['total'], row['local'], row['other'])
                   for row in summary['days'] if row['total']}
        self.assertEqual(nonzero, {'2026-10-28': (5, 2.5, 2.5),
                                   '2026-10-29': (5, 2.5, 2.5)})
        for key in ('total', 'local', 'other'):
            self.assertEqual(sum(decimal(row[key] or 0) for row in summary['days']),
                             decimal(summary[key]))
        for day in nonzero:
            self.assertEqual(summary['ledger']['days'][day]['points'], 5)

        # Re-projecting the identical official sample must not apply it twice.
        projected_before = copy.deepcopy(summary['ledger'])
        repeated = quota_breakdown(summary['ledger'], 90, reset, '2026-10-29',
                                   sample_at=sample_at)
        self.assertEqual(summary['ledger'], projected_before)
        self.assertEqual(repeated['ledger'], projected_before)
        self.assertEqual(repeated['days'], summary['days'])

    def test_p2_table_rounding_preserves_zero_cells_and_minimizes_row_error(self):
        cases = (
            ('pure_local_last_day', [(.6, .4)] * 6 + [(10, 0)], ('16', '14', '2')),
            ('pure_other_last_day', [(.4, .6)] * 6 + [(0, 10)], ('16', '2', '14')),
            ('tiny_daily_totals', [(.2, .3)] * 3, ('2', '1', '1')),
        )
        for name, pairs, expected in cases:
            with self.subTest(case=name):
                start = dt.date(2026, 10, 28)
                keys = [(start + dt.timedelta(days=i)).isoformat() for i in range(len(pairs))]
                local = sum((decimal(pair[0]) for pair in pairs), Decimal(0))
                other = sum((decimal(pair[1]) for pair in pairs), Decimal(0))
                remaining = float(Decimal(100) - local - other)
                self.assertGreaterEqual(remaining, 0)
                self.assertLessEqual(remaining, 100)
                reset = at('2026-11-04')
                source = {'reset': reset, 'firstAt': at(keys[0]), 'firstRemaining': 100,
                          'lastAt': at(keys[-1]), 'lastRemaining': remaining,
                          'localUsed': float(local), 'otherUsed': float(other),
                          'localDays': {key: {'points': pair[0]} for key, pair in zip(keys, pairs)},
                          'otherDays': {key: {'points': pair[1]} for key, pair in zip(keys, pairs)},
                          'days': {key: {'points': float(decimal(pair[0]) + decimal(pair[1]))}
                                   for key, pair in zip(keys, pairs)}}
                original = copy.deepcopy(source)
                summary = quota_breakdown(source, remaining, reset, keys[-1])
                self.assertEqual(source, original)
                self.assertEqual(decimal(summary['local']), local)
                self.assertEqual(decimal(summary['other']), other)
                by_day = {row['day'].isoformat(): row for row in summary['days']}
                for day, pair in zip(keys, pairs):
                    self.assertEqual((by_day[day]['local'], by_day[day]['other']), pair)
                for key, value in zip(('total', 'local', 'other'), expected):
                    self.assertRegex(summary['labels'][key], r'\A[0-9]+\Z')
                    self.assertEqual(decimal(summary['labels'][key]), decimal(value))
                    self.assertEqual(sum(decimal(row['labels'][key]) for row in summary['days']),
                                     decimal(summary['labels'][key]))

                for row in summary['days']:
                    labels = row['labels']
                    for value in labels.values():
                        self.assertRegex(value, r'\A[0-9]+\Z')
                    self.assertEqual(decimal(labels['local']) + decimal(labels['other']),
                                     decimal(labels['total']))
                    for key in ('local', 'other'):
                        units = decimal(labels[key])
                        self.assertIn(units, rounding_choices(row[key]), (name, row['day'], key, row))
                        if not row[key]:
                            self.assertEqual(units, 0, (name, row['day'], key, row))

                # This oracle also covers cases where zero row error is impossible.
                actual_error = sum((abs(decimal(row['labels']['total']) - decimal(row['total'] or 0))
                                    for row in summary['days']), Decimal(0))
                targets = [int(summary['labels'][key]) for key in ('local', 'other')]
                best = minimum_row_error(summary['days'], *targets)
                self.assertIsNotNone(best, 'Fixture must admit a closed floor/ceil table')
                self.assertEqual(actual_error, best)


if __name__ == '__main__':
    unittest.main()
