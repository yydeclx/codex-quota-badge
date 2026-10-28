import time
import unittest

from quota_badge import weekly_quota,weekly_quota_from_opencodex_report
from hover_panel import render_quota_chart


class QuotaWindowsTests(unittest.TestCase):
    def test_opencodex_292_provider_quota_matches_native_weekly_view(self):
        result=weekly_quota_from_opencodex_report({'reports':[{'provider':'openai','updatedAt':1792923189817,'quota':{
            'weeklyPercent':5,'aggregation':{'currentAccount':{'plan':'prolite','quota':{'weeklyPercent':5,'weeklyResetAt':1793513441}}}}}]})
        self.assertEqual(result['remaining'],95)
        self.assertEqual(result['resetsAt'],1793513441)
        self.assertIsNone(result['fiveHour'])
        self.assertEqual(result['planType'],'prolite')
        self.assertEqual(result['quotaSource'],'opencodex provider quota')
    def test_reads_five_hour_and_weekly_windows_by_duration(self):
        now = time.time()
        result = weekly_quota({'rateLimitsByLimitId': {'codex': {
            'primary': {'usedPercent': 53, 'windowDurationMins': 300, 'resetsAt': now + 3600},
            'secondary': {'usedPercent': 40, 'windowDurationMins': 10080, 'resetsAt': now + 86400},
        }}})
        self.assertEqual(result['remaining'], 60)
        self.assertEqual(result['fiveHour']['remaining'], 47)
        self.assertEqual(result['resetsAt'], now + 86400)

    def test_five_hour_window_is_optional(self):
        now = time.time()
        result = weekly_quota({'rateLimits': {
            'primary': {'usedPercent': 25, 'windowDurationMins': 10080, 'resetsAt': now + 86400},
        }})
        self.assertEqual(result['remaining'], 75)
        self.assertIsNone(result['fiveHour'])

    def test_pro_weekly_window_accepts_null_secondary(self):
        now = time.time()
        result = weekly_quota({'rateLimitsByLimitId': {'codex': {
            'primary': {'usedPercent': 1, 'windowDurationMins': 10080, 'resetsAt': now + 86400},
            'secondary': None,
            'planType': 'prolite',
        }}})
        self.assertEqual(result['remaining'], 99)
        self.assertIsNone(result['fiveHour'])
        self.assertEqual(result['planType'], 'prolite')

    def test_combined_card_renders_at_expected_size(self):
        now = time.time()
        image, actions = render_quota_chart(
            1, 60, now + 86400, {'days': {}}, None, None,
            {'remaining': 47, 'resetsAt': now + 3600})
        self.assertEqual(image.size, (264, 251))
        self.assertEqual(len(actions), 7)

    def test_stacked_daily_quota_and_hover_render(self):
        now=time.time();cycle={
            'days':{'2026-10-23':{'points':5}},
            'localDays':{'2026-10-23':{'points':3}},
            'localUsed':3,'otherUsed':2,'unallocated':0,
        }
        image,actions=render_quota_chart(1,97,now+86400,cycle,0,'2026-10-23',None,'prolite')
        self.assertEqual(image.size,(264,251));self.assertEqual(len(actions),7)

    def test_current_day_bar_uses_cycle_remainder_not_cumulative_total(self):
        now=time.time();cycle={
            'days':{'2026-10-26':{'points':30}},
            'localDays':{'2026-10-26':{'points':28}},
            'localUsed':28,'otherUsed':2,'unallocated':0,
        }
        image,actions=render_quota_chart(1,61,now+86400,cycle,None,'2026-10-27',None,'prolite')
        self.assertEqual(image.size,(264,251));self.assertEqual(len(actions),7)

    def test_reset_signal_forecast_expands_card(self):
        now=time.time();cycle={'days':{},'localDays':{},'localUsed':0,'otherUsed':0,'unallocated':0}
        signal={'state':'forecast','probability24h':32,'probability48h':53,'source':'codex-reset.com','stale':False}
        image,actions=render_quota_chart(1,97,now+86400,cycle,None,'2026-10-23',None,'prolite',signal)
        self.assertEqual(image.size,(264,319));self.assertEqual(len(actions),7)

    def test_explicit_and_account_confirmed_states_render(self):
        now=time.time();cycle={'days':{},'localDays':{},'localUsed':0,'otherUsed':0,'unallocated':0}
        states=[
            {'state':'signal','explicitSignal':{'summary':'Lands tomorrow before 10am PST.','targetAt':now+3600}},
            {'state':'forecast','accountConfirmation':{'from':40,'to':100}},
        ]
        for signal in states:
            with self.subTest(signal=signal):
                image,_=render_quota_chart(1,97,now+86400,cycle,None,'2026-10-23',None,'prolite',signal)
                self.assertEqual(image.size,(264,319))

    def test_countdown_never_overwrites_weekly_remaining(self):
        now=time.time();cycle={'days':{},'localDays':{},'localUsed':0,'otherUsed':0,'unallocated':0}
        signal={'state':'signal','freshness':'fresh','explicitSignal':{'targetAt':now+40400}}
        image,_=render_quota_chart(1,97,now+86400,cycle,None,'2026-10-23',None,'prolite',signal)
        self.assertEqual(image.size,(264,319))


if __name__ == '__main__':
    unittest.main()


