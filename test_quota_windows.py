import time
import unittest
import copy
import json
from unittest.mock import patch

from quota_badge import weekly_quota,weekly_quota_from_opencodex_report,fetch_quota
from hover_panel import render_quota_chart


class QuotaWindowsTests(unittest.TestCase):
    def current_report(self):
        # Sanitized shape observed from the installed 2.92 CLI. Pool and
        # current-account values intentionally differ to detect scope mixing.
        return {'reports':[{'provider':'openai','updatedAt':1793359414350,
            'quota':{'fiveHourPercent':92,'weeklyPercent':31},
            'aggregation':{'weekly':{'usedPercent':31,'nextRecoveryAt':1793780200000},
                'fiveHour':{'usedPercent':92,'nextRecoveryAt':1793361504000},
                'currentAccount':{'plan':'prolite','quota':{
                    'weeklyPercent':28,'weeklyResetAt':1793906017,'updatedAt':1793359619911}}}}]}

    def test_current_cli_sibling_aggregation_uses_paired_account_observation(self):
        report=self.current_report();before=copy.deepcopy(report)
        result=weekly_quota_from_opencodex_report(report)
        self.assertEqual(result['remaining'],72)
        self.assertEqual(result['resetsAt'],1793906017)
        self.assertEqual(result['updatedAt'],1793359619.911)
        self.assertEqual(result['quotaScope'],'current-account')
        self.assertEqual(result['planType'],'prolite')
        self.assertIsNone(result['fiveHour']) # Pool's five-hour bucket is unrelated.
        self.assertEqual(report,before)

    def test_current_report_never_starts_rpc_fallback(self):
        with patch('quota_badge._provider_quota_output',return_value=json.dumps(self.current_report())),patch('quota_badge.find_codex',side_effect=AssertionError('unexpected fallback')):
            self.assertEqual(fetch_quota()['remaining'],72)

    def test_pool_fallback_keeps_both_windows_in_pool_scope(self):
        report=self.current_report();report['reports'][0]['aggregation'].pop('currentAccount')
        result=weekly_quota_from_opencodex_report(report)
        self.assertEqual((result['remaining'],result['resetsAt']),(69,1793780200))
        self.assertEqual(result['fiveHour'],{'remaining':8,'resetsAt':1793361504})
        self.assertEqual(result['quotaScope'],'account-pool');self.assertIsNone(result['planType'])

    def test_incomplete_account_cannot_borrow_pool_percent_or_reset(self):
        for missing in ('weeklyPercent','weeklyResetAt'):
            report=self.current_report();del report['reports'][0]['aggregation']['currentAccount']['quota'][missing]
            result=weekly_quota_from_opencodex_report(report)
            self.assertEqual((result['remaining'],result['resetsAt']),(69,1793780200))

    def test_direct_provider_windows_preserve_zero_and_seconds(self):
        report={'reports':[{'provider':'openai','updatedAt':1793359619,'quota':{
            'weeklyPercent':0,'weeklyResetAt':1793906017,'fiveHourPercent':100,'fiveHourResetAt':1793361504000}}]}
        result=weekly_quota_from_opencodex_report(report)
        self.assertEqual(result['remaining'],100);self.assertEqual(result['updatedAt'],1793359619)
        self.assertEqual(result['fiveHour'],{'remaining':0,'resetsAt':1793361504})

    def test_top_level_aggregation_precedes_obsolete_nested_metadata(self):
        report=self.current_report();report['reports'][0]['quota']['aggregation']={'currentAccount':{'quota':{'weeklyPercent':99,'weeklyResetAt':1800000000}}}
        self.assertEqual(weekly_quota_from_opencodex_report(report)['remaining'],72)

    def test_malformed_metadata_raises_value_error_for_bounded_fallback(self):
        for field in ('aggregation','quota'):
            report=self.current_report();report['reports'][0][field]=['bad']
            with self.assertRaises(ValueError):weekly_quota_from_opencodex_report(report)

    def test_missing_or_invalid_complete_window_is_rejected(self):
        for value in (None,True,float('inf'),-1,101):
            report=self.current_report();agg=report['reports'][0]['aggregation']
            agg.pop('weekly');agg['currentAccount']['quota']['weeklyPercent']=value
            with self.assertRaises(ValueError):weekly_quota_from_opencodex_report(report)
        report=self.current_report();agg=report['reports'][0]['aggregation'];agg.pop('weekly')
        agg['currentAccount']['quota']['weeklyResetAt']=True
        with self.assertRaises(ValueError):weekly_quota_from_opencodex_report(report)

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


