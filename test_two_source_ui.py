"""Render synthetic two-source quota data without accounts, history or Tk windows."""
import copy
import datetime as dt
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw
from hover_panel import render_quota_chart
from sidebar_details import Details


LOCAL = (93, 93, 89, 255)
OTHER = (185, 184, 179, 255)


def example(pairs=((35, 2),)):
    """New core contract: local 35 + other 2 + remaining 63 = 100."""
    start = dt.date(2026, 10, 28)
    first = dt.datetime(2026, 10, 28, 14, 57, 53).timestamp()
    reset = dt.datetime(2026, 11, 4, 14, 35, 29).timestamp()
    days = []
    for index in range(7):
        local, other = pairs[index] if index < len(pairs) else (0, 0)
        total = local + other
        days.append({'day': start + dt.timedelta(days=index), 'total': total,
                     'local': local, 'other': other,
                     'labels': {key: f'{value:g}' for key, value in
                                (('total', total), ('local', local), ('other', other))}})
    source = {'reset': reset, 'firstAt': first, 'lastAt': first + 3600,
              'firstRemaining': 98, 'lastRemaining': 63, 'corrections': 0,
              'localUsed': 35, 'otherUsed': 2,
              'days': {row['day'].isoformat(): {'points': row['total'], 'samples': 1}
                       for row in days},
              'localDays': {row['day'].isoformat(): {'points': row['local']} for row in days},
              'otherDays': {row['day'].isoformat(): {'points': row['other']} for row in days}}
    summary = {'total': 37, 'local': 35, 'other': 2,
               'labels': {'total': '37', 'local': '35', 'other': '2'},
               'days': days, 'ledger': copy.deepcopy(source)}
    return source, summary


class TwoSourceUiTests(unittest.TestCase):
    def render(self, source, summary, scale=1, focus=None, signal=None, five_hour=None):
        texts, bars = [], []
        original_text = ImageDraw.ImageDraw.text
        original_composite = Image.Image.alpha_composite

        def record_text(draw, xy, text, *args, **kwargs):
            texts.append((str(text), xy, kwargs.get('anchor')))
            return original_text(draw, xy, text, *args, **kwargs)

        def record_composite(image, overlay, *args, **kwargs):
            # Capture actual painted stacks before final antialiasing downsizing.
            if overlay.width == round(16 * scale * 3):
                bars.append(overlay.copy())
            return original_composite(image, overlay, *args, **kwargs)

        with patch('hover_panel.quota_breakdown', return_value=summary) as breakdown, \
                patch.object(ImageDraw.ImageDraw, 'text', record_text), \
                patch.object(Image.Image, 'alpha_composite', record_composite):
            image, actions = render_quota_chart.__wrapped__(
                scale, 63, source['reset'], source, focus, '2026-10-30',
                five_hour, 'prolite', signal)
        breakdown.assert_called_once_with(source, 63, source['reset'], '2026-10-30', None)
        return image, actions, texts, bars

    def assert_two_sources(self, texts, scale=1, signal=None):
        labels = [text for text, _, _ in texts]
        for removed in ('来源待定', '待定', '日期待定位'):
            self.assertFalse(any(removed in label for label in labels), labels)
        legend_y = round((232 + (68 if signal is not None else 0)) * scale * 3)
        legend = [text for text, xy, anchor in texts if xy[1] == legend_y and anchor == 'lm']
        self.assertEqual(legend, ['本机 35%', '非本机 2%'])

    def assert_closed_stacks(self, bars, summary):
        positive = [row for row in summary['days'] if row['total'] and row['total'] > 0]
        self.assertEqual(len(bars), len(positive))
        self.assertAlmostEqual(sum(row['total'] or 0 for row in summary['days']), 37)
        self.assertAlmostEqual(sum(row['local'] for row in summary['days']), 35)
        self.assertAlmostEqual(sum(row['other'] for row in summary['days']), 2)
        for bar, row in zip(bars, positive):
            with self.subTest(day=row['day']):
                # The center of every pixel row must belong to one source:
                # a transparent gap or a third fill breaks the displayed total.
                column = [bar.getpixel((bar.width // 2, y)) for y in range(bar.height)]
                self.assertTrue(set(column) <= {LOCAL, OTHER}, set(column))
                self.assertEqual(column.count(LOCAL) + column.count(OTHER), bar.height)
                self.assertAlmostEqual(row['local'] + row['other'], row['total'])
                self.assertAlmostEqual(float(row['labels']['local']) + float(row['labels']['other']),
                                       float(row['labels']['total']))
                self.assertLessEqual(abs(column.count(LOCAL) - bar.height * row['local'] / row['total']), .51)
                self.assertLessEqual(abs(column.count(OTHER) - bar.height * row['other'] / row['total']), .51)
                self.assertEqual(column, [OTHER] * column.count(OTHER) + [LOCAL] * column.count(LOCAL))

    def test_cycle_total_legend_and_two_color_stack_at_existing_dpi_sizes(self):
        source, summary = example()
        before = copy.deepcopy(source)
        for scale in (1, 1.25, 1.5, 2, 2.5):
            with self.subTest(scale=scale):
                image, actions, texts, bars = self.render(source, summary, scale)
                self.assertEqual(image.size, (round(264 * scale), round(251 * scale)))
                self.assertEqual(len(actions), 7)
                self.assertIn('周期总37% · 归因估算', [text for text, _, _ in texts])
                self.assertIn('63%', [text for text, _, _ in texts])
                self.assertIn('37%', [text for text, _, _ in texts])
                self.assert_two_sources(texts, scale)
                self.assert_closed_stacks(bars, summary)
        self.assertEqual(source, before)

    def test_daily_hover_lists_total_local_other_without_resizing_or_moving_hitboxes(self):
        source, summary = example(((12, 1), (11, 0), (12, 1)))
        initial, hitboxes, _, _ = self.render(source, summary)
        for index, row in enumerate(summary['days']):
            with self.subTest(day=row['day']):
                image, actions, texts, bars = self.render(source, summary, focus=index)
                labels = row['labels']
                self.assertIn(f"{row['day']:%m/%d}  总{labels['total']}% · 本机{labels['local']}% · 非本机{labels['other']}%",
                              [text for text, _, _ in texts])
                self.assertEqual(image.size, initial.size)
                self.assertEqual(actions, hitboxes)
                self.assert_two_sources(texts)
                self.assert_closed_stacks(bars, summary)

    def test_fractional_daily_stacks_and_single_source_days_stay_closed(self):
        for pairs in (((12.4, .6), (11.1, .9), (11.5, .5)), ((35, 0), (0, 2))):
            with self.subTest(pairs=pairs):
                source, summary = example(pairs)
                _, _, texts, bars = self.render(source, summary, scale=2)
                self.assert_two_sources(texts, scale=2)
                self.assert_closed_stacks(bars, summary)

    def test_no_record_and_reset_monitor_preserve_other_metrics_and_geometry(self):
        source, summary = example()
        summary['days'][2]['total'] = None
        signal = {'state': 'forecast', 'probability24h': 32, 'probability48h': 53,
                  'confidence': 'low', 'freshness': 'fresh'}
        five_hour = {'remaining': 47, 'resetsAt': source['firstAt'] + 3600}
        image, actions, texts, bars = self.render(source, summary, focus=2,
                                                  signal=signal, five_hour=five_hour)
        self.assertEqual(image.size, (264, 319))
        self.assertEqual(len(actions), 7)
        labels = [text for text, _, _ in texts]
        for label in ('10/30 · 尚未记录', '5 小时额度', '47%', '实际周额度', '63%', '32%', '53%'):
            self.assertIn(label, labels)
        self.assert_two_sources(texts, signal=signal)
        self.assert_closed_stacks(bars, summary)

    def test_status_center_uses_only_two_sources_and_explains_estimation_with_mock_cards(self):
        source, summary = example(((12, 1), (11, 0), (12, 1)))
        source['corrections'] = 2
        details = Details.__new__(Details)
        details.snapshot = {'quota': {'remaining': 63}, 'quotaHistory': source}
        details.p = lambda value: value
        details.card = Mock(return_value=Mock())
        details.label = Mock(return_value=Mock())

        class FixedDate(dt.date):
            @classmethod
            def today(cls):
                return cls(2026, 10, 30)

        with patch('quota_accounting.quota_breakdown', return_value=summary) as breakdown, \
                patch('sidebar_details.dt', SimpleNamespace(date=FixedDate, datetime=dt.datetime)), \
                patch('sidebar_details.time.time', return_value=source['lastAt']):
            details.quota()
        breakdown.assert_called_once_with(source, 63, source['reset'], sample_at=None)
        cards = {call.args[0]: call.args[1] if len(call.args) > 1 else ''
                 for call in details.card.call_args_list}
        self.assertEqual(cards['周额度'], '剩余 63%')
        self.assertEqual(cards['本周期 · 消耗归因（估算）'], '总 37% · 本机 35% · 非本机 2%')
        daily = [call.args[1] for call in details.label.call_args_list]
        self.assertEqual(daily, ['10/28     总 13% · 本机 12% · 非本机 1%',
                                 '10/29     总 11% · 本机 11% · 非本机 0%',
                                 '10/30     总 13% · 本机 12% · 非本机 1%'])
        note = cards['记录范围']
        for text in ('开始采样', '归因估算', '本机用量/活动', '断档按比例估算分配', '非官方精确归因', '2 次额度回调'):
            self.assertIn(text, note)
        combined = '\n'.join([*cards.keys(), *cards.values(), *daily])
        for removed in ('来源待定', '待定', '日期待定位'):
            self.assertNotIn(removed, combined)


if __name__ == '__main__':
    unittest.main()
