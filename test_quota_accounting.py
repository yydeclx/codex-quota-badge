"""Two-source conservation and interval regressions, using temporary history only."""
import copy
import datetime as dt
from decimal import Decimal
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from PIL import ImageDraw
from hover_panel import render_quota_chart
from quota_accounting import quota_breakdown,reconcile_cycle
from sidebar_modules import QuotaHistory


def stamp(day='2026-10-28',clock='14:57:53'):
    return dt.datetime.fromisoformat(day+'T'+clock).timestamp()

def example():
    return {'reset':stamp('2026-11-04','14:35:29'),'firstAt':stamp(),'firstRemaining':98,
            'lastAt':stamp(clock='23:00:00'),'lastRemaining':63,
            'days':{'2026-10-28':{'points':35,'samples':20}},
            'localDays':{'2026-10-28':{'points':33,'samples':10}},
            'otherDays':{'2026-10-28':{'points':2,'samples':2}},
            'localUsed':33,'otherUsed':2,'corrections':0}


class QuotaAccountingTests(unittest.TestCase):
    def assert_closed(self,cycle):
        forbidden={'unknownUsed','unknownDays','unallocated','unknown','undated'}
        self.assertFalse(forbidden.intersection(cycle))
        dec=lambda value:Decimal(str(value))
        total=dec(cycle['localUsed'])+dec(cycle['otherUsed'])
        self.assertEqual(sum((dec(row['points']) for row in cycle['days'].values()),Decimal(0)),total)
        for aggregate,daily in (('localUsed','localDays'),('otherUsed','otherDays')):
            self.assertEqual(sum((dec(row['points']) for row in cycle[daily].values()),Decimal(0)),dec(cycle[aggregate]))
        self.assertTrue((set(cycle['localDays'])|set(cycle['otherDays']))<=set(cycle['days']))
        for day,row in cycle['days'].items():
            values=[dec(cycle[kind].get(day,{}).get('points',0)) for kind in ('localDays','otherDays')]
            self.assertTrue(all(value>=0 and value==value.quantize(Decimal('.0001')) for value in values))
            self.assertEqual(sum(values),dec(row['points']))
        summary=quota_breakdown(cycle,reset=cycle.get('reset'))
        self.assertFalse(forbidden.intersection(summary))
        self.assertEqual(dec(summary['local'])+dec(summary['other']),dec(summary['total']))
        self.assertEqual(set(summary['labels']),{'total','local','other'})
        self.assertEqual(dec(summary['labels']['local'])+dec(summary['labels']['other']),dec(summary['labels']['total']))
        for row in summary['days']:
            self.assertFalse(forbidden.intersection(row))
            self.assertEqual(dec(row['local'])+dec(row['other']),dec(row['total'] or 0))
            self.assertEqual(set(row['labels']),{'total','local','other'})
            self.assertEqual(dec(row['labels']['local'])+dec(row['labels']['other']),dec(row['labels']['total']))
        for key in ('total','local','other'):
            self.assertEqual(sum((dec(row['labels'][key]) for row in summary['days']),Decimal(0)),dec(summary['labels'][key]))
        for row in [summary,*summary['days']]:
            for value in row['labels'].values():
                self.assertIsInstance(value,str)
                self.assertRegex(value,r'^[0-9]+\Z')

    def test_first_sample_gap_uses_smoothed_source_prior_on_its_day(self):
        source=example();original=copy.deepcopy(source);summary=quota_breakdown(source,63,source['reset'],'2026-10-28')
        self.assertEqual((summary['total'],summary['local'],summary['other']),(37,34.8378,2.1622))
        self.assertEqual(summary['days'][0]['total'],37)
        self.assertEqual((summary['days'][0]['local'],summary['days'][0]['other']),(34.8378,2.1622))
        self.assertEqual(source,original,'Projection mutated the live input')
        self.assert_closed(summary['ledger'])

    def test_confirmed_35_local_and_2_other_gives_a_37_stack(self):
        source=example();source['localUsed']=35;source['localDays']['2026-10-28']['points']=35;source['days']['2026-10-28']['points']=37
        result=quota_breakdown(source,63,source['reset'],'2026-10-28')
        self.assertEqual((result['days'][0]['total'],result['days'][0]['local'],result['days'][0]['other']),(37,35,2))
        self.assert_closed(result['ledger'])

    def test_initial_gap_spanning_midnight_is_split_by_elapsed_seconds(self):
        source=example();source['reset']=stamp('2026-11-04','23:55:00');source['firstAt']=stamp('2026-10-29','00:05:00')
        source['lastAt']=stamp('2026-10-29','10:00:00')
        for key in ('days','localDays','otherDays'):source[key]={'2026-10-29':source[key]['2026-10-28']}
        result=quota_breakdown(source,63,source['reset'],'2026-10-29')
        self.assertEqual(result['ledger']['days']['2026-10-28']['points'],1)
        self.assertEqual(result['ledger']['days']['2026-10-29']['points'],36)
        self.assert_closed(result['ledger'])

    def test_normalization_is_idempotent_and_does_not_reuse_initial_gap(self):
        source=reconcile_cycle(example())
        self.assertEqual(source,reconcile_cycle(source))
        source['lastAt']=stamp('2026-10-29','00:05:00');source['lastRemaining']=62
        result=reconcile_cycle(source)
        self.assertGreaterEqual(result['days']['2026-10-28']['points'],37)
        self.assertEqual(sum(row['points'] for row in result['days'].values()),38)
        self.assertEqual(result,reconcile_cycle(result))
        self.assert_closed(result)

    def test_last_partial_calendar_day_is_not_dropped(self):
        source=reconcile_cycle(example());source['lastAt']=stamp('2026-11-04','09:00:00');source['lastRemaining']=62
        source['localUsed']+=1;source['localDays']['2026-11-04']={'points':1}
        result=quota_breakdown(source,62,source['reset'],'2026-11-04')
        self.assertEqual(len(result['days']),8)
        self.assertEqual(result['days'][-1]['day'].isoformat(),'2026-11-04')
        self.assertEqual(result['days'][-1]['total'],1)
        self.assert_closed(result['ledger'])

    def test_previous_days_are_not_counted_again_in_todays_bar(self):
        source=example();source['lastAt']=stamp('2026-10-29','10:00:00');source['lastRemaining']=54
        source['days']['2026-10-29']={'points':9};source['localDays']['2026-10-29']={'points':9};source['localUsed']=42
        result=quota_breakdown(source,54,source['reset'],'2026-10-29')
        self.assertEqual((result['days'][0]['total'],result['days'][1]['total']),(37,9))
        self.assertEqual(sum(row['total'] or 0 for row in result['days']),46)

    def test_migration_preserves_original_and_only_writes_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'history.json';original={'version':4,'cycles':[example()]}
            path.write_text(json.dumps(original),encoding='utf-8');old=path.read_bytes()
            first=QuotaHistory(path);saved=path.read_bytes()
            backups=[p for p in (path.with_name('history.schema5-backup.json'),path.with_name('history.schema4-backup.json')) if p.exists()]
            self.assertTrue(backups);self.assertTrue(all(p.read_bytes()==old for p in backups))
            self.assertEqual(first.data['version'],6)
            self.assertEqual((first.view()['localUsed'],first.view()['otherUsed']),(34.8378,2.1622))
            with patch('sidebar_modules.atomic_json') as write:
                second=QuotaHistory(path);write.assert_not_called()
            self.assertEqual(second.view(),first.view());self.assertEqual(path.read_bytes(),saved)

    def test_active_incomplete_request_can_match_before_usage_log_arrives(self):
        with tempfile.TemporaryDirectory() as directory:
            history=QuotaHistory(Path(directory)/'history.json');start=stamp();reset=stamp('2026-11-04','14:35:29')
            history.observe({'remaining':100,'resetsAt':reset,'updatedAt':start})
            history.observe({'remaining':99,'resetsAt':reset,'updatedAt':start+60},local_active=True)
            self.assertEqual(history.view()['localUsed'],1)
            self.assertEqual(history.view()['otherUsed'],0)

    def test_missing_or_regressing_local_data_is_not_nonlocal_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            history=QuotaHistory(Path(directory)/'history.json');start=stamp();reset=start+604800
            usage={'date':'2026-10-28','totalTokens':100,'requests':1,'updatedAt':start}
            history.observe({'remaining':100,'resetsAt':reset,'updatedAt':start},usage)
            history.observe({'remaining':99,'resetsAt':reset,'updatedAt':start+60},{**usage,'updatedAt':start-10,'totalTokens':200})
            self.assertEqual((history.view()['localUsed'],history.view()['otherUsed']),(.5,.5))
            self.assertEqual(history.view()['lastLocalUsage']['updatedAt'],start)

    def test_unchanged_complete_counters_match_other_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            history=QuotaHistory(Path(directory)/'history.json');start=stamp();reset=start+604800
            usage={'date':'2026-10-28','totalTokens':100,'requests':1,'updatedAt':start}
            history.observe({'remaining':100,'resetsAt':reset,'updatedAt':start},usage)
            history.observe({'remaining':99,'resetsAt':reset,'updatedAt':start+60},{**usage,'updatedAt':start+60})
            self.assertEqual((history.view()['localUsed'],history.view()['otherUsed']),(0,1))

    def test_long_gap_does_not_assign_all_to_a_current_local_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            history=QuotaHistory(Path(directory)/'history.json');start=stamp(clock='15:00:00');reset=start+604800
            history.observe({'remaining':100,'resetsAt':reset,'updatedAt':start})
            history.observe({'remaining':90,'resetsAt':reset,'updatedAt':start+4*3600},local_active=True)
            self.assertEqual((history.view()['localUsed'],history.view()['otherUsed']),(5.3125,4.6875))
            self.assertEqual(history.view()['days']['2026-10-28']['points'],10)
            self.assert_closed(history.view())

    def test_midnight_local_counter_rollover_keeps_source_and_splits_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            history=QuotaHistory(Path(directory)/'history.json');start=stamp(clock='23:59:30');reset=start+604800
            usage={'date':'2026-10-28','totalTokens':100,'requests':4,'updatedAt':start}
            history.observe({'remaining':100,'resetsAt':reset,'updatedAt':start},usage)
            history.observe({'remaining':99,'resetsAt':reset,'updatedAt':start+60},{'date':'2026-10-29','totalTokens':10,'requests':1,'updatedAt':start+60})
            self.assertEqual(history.view()['localUsed'],1)
            self.assertEqual(history.view()['localDays']['2026-10-28']['points'],.5)
            self.assertEqual(history.view()['localDays']['2026-10-29']['points'],.5)
            self.assert_closed(history.view())

    def test_unchanged_quota_and_usage_does_not_rewrite_each_heartbeat(self):
        with tempfile.TemporaryDirectory() as directory:
            history=QuotaHistory(Path(directory)/'history.json');start=stamp();reset=start+604800
            usage={'date':'2026-10-28','totalTokens':100,'requests':1,'updatedAt':start,'inputTokens':90,'cachedInputTokens':80}
            snapshot={'remaining':100,'resetsAt':reset,'updatedAt':start};history.observe(snapshot,usage)
            with patch('sidebar_modules.atomic_json') as write:
                for _ in range(100):history.observe(snapshot,usage)
                write.assert_not_called()

    def test_rebound_and_reset_jitter_preserve_a_closed_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            history=QuotaHistory(Path(directory)/'history.json');start=stamp();reset=start+604800
            for index,remaining in enumerate((100,80,90,50,100)):
                history.observe({'remaining':remaining,'resetsAt':reset+(15 if index%2 else 0),'updatedAt':start+index*60},local_active=True)
                self.assert_closed(history.view())
            self.assertEqual(len(history.data['cycles']),1)
            self.assertEqual(history.view()['localUsed'],0)
            self.assertEqual(history.view()['corrections'],2)
            saved=copy.deepcopy(history.view())
            history.observe({'remaining':50,'resetsAt':reset+86400,'updatedAt':start})
            self.assertEqual(history.view(),saved)

    def test_old_counter_baseline_does_not_claim_recent_local_activity(self):
        with tempfile.TemporaryDirectory() as directory:
            history=QuotaHistory(Path(directory)/'history.json');start=stamp(clock='14:00:00');reset=start+604800
            usage={'date':'2026-10-28','totalTokens':0,'requests':0,'updatedAt':start}
            history.observe({'remaining':100,'resetsAt':reset,'updatedAt':start},usage)
            history.observe({'remaining':100,'resetsAt':reset,'updatedAt':start+3600},None)
            history.observe({'remaining':99,'resetsAt':reset,'updatedAt':start+3660},{**usage,'totalTokens':100,'requests':1,'updatedAt':start+3660})
            self.assertEqual((history.view()['localUsed'],history.view()['otherUsed']),(.5,.5))

    def test_midnight_zero_counter_does_not_prove_other_device(self):
        with tempfile.TemporaryDirectory() as directory:
            history=QuotaHistory(Path(directory)/'history.json');start=stamp(clock='23:59:00');reset=start+604800
            usage={'date':'2026-10-28','totalTokens':100,'requests':2,'updatedAt':start}
            history.observe({'remaining':100,'resetsAt':reset,'updatedAt':start},usage)
            history.observe({'remaining':99,'resetsAt':reset,'updatedAt':start+120},{'date':'2026-10-29','totalTokens':0,'requests':0,'updatedAt':start+120})
            self.assertEqual((history.view()['localUsed'],history.view()['otherUsed']),(.5,.5))

    def test_refunded_initial_gap_cannot_absorb_later_days_drop(self):
        with tempfile.TemporaryDirectory() as directory:
            history=QuotaHistory(Path(directory)/'history.json');start=stamp(clock='23:59:00');reset=start+604800
            history.observe({'remaining':98,'resetsAt':reset,'updatedAt':start})
            history.observe({'remaining':99,'resetsAt':reset,'updatedAt':start+30})
            previous=copy.deepcopy(history.view()['days'])
            history.observe({'remaining':98,'resetsAt':reset,'updatedAt':start+120})
            current=history.view()
            for day,row in previous.items():self.assertGreaterEqual(current['days'][day]['points'],row['points'])
            self.assertGreater(current['days']['2026-10-29']['points'],0)
            self.assert_closed(current)

    def test_preview_and_persisted_refund_apply_the_same_source_reduction(self):
        source=example();preview=quota_breakdown(source,65,source['reset'],'2026-10-28')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'history.json';path.write_text(json.dumps({'version':4,'cycles':[source]}),encoding='utf-8')
            history=QuotaHistory(path);history.observe({'remaining':65,'resetsAt':source['reset'],'updatedAt':source['lastAt']+60})
            actual=quota_breakdown(history.view(),65,source['reset'],'2026-10-28')
        self.assertEqual((preview['local'],preview['other']),(actual['local'],actual['other']))
        self.assertEqual(preview['ledger']['days'],actual['ledger']['days'])
        self.assert_closed(actual['ledger'])

    def test_new_window_never_borrows_the_previous_cycles_sources(self):
        source=example();new_reset=stamp('2026-11-05','09:00:00')
        result=quota_breakdown(source,98,new_reset,'2026-10-29')
        self.assertEqual((result['local'],result['other']),(1,1))
        self.assert_closed(result['ledger'])

    def test_same_stamp_counter_receipt_cannot_use_an_expired_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            history=QuotaHistory(Path(directory)/'history.json');start=stamp();reset=start+604800
            usage={'date':'2026-10-28','totalTokens':0,'requests':0,'updatedAt':start}
            history.observe({'remaining':100,'resetsAt':reset,'updatedAt':start},usage)
            sample={'remaining':100,'resetsAt':reset,'updatedAt':start+600};history.observe(sample)
            later={**usage,'totalTokens':100,'requests':1,'updatedAt':start+601}
            history.observe(sample,later)
            self.assertIsNone(history.view()['pendingLocalAt'])
            history.observe({'remaining':99,'resetsAt':reset,'updatedAt':start+660},later)
            self.assertEqual(history.view()['localUsed'],0)

    def test_reset_boundary_jitter_does_not_duplicate_the_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            history=QuotaHistory(Path(directory)/'history.json');start=stamp();reset=start+604800
            history.observe({'remaining':100,'resetsAt':reset,'updatedAt':start})
            history.observe({'remaining':99,'resetsAt':reset+15,'updatedAt':reset+5})
            self.assertEqual(len(history.data['cycles']),1);self.assertEqual(history.view()['firstAt'],start)
            history.observe({'remaining':100,'resetsAt':reset+604800,'updatedAt':reset+30})
            self.assertEqual(len(history.data['cycles']),2)

    def test_simulated_activity_refunds_and_midnight_always_conserve(self):
        with tempfile.TemporaryDirectory() as directory:
            history=QuotaHistory(Path(directory)/'history.json');now=stamp();reset=now+604800
            rng=random.Random(37);remaining=98;tokens=requests=0;last_day=None
            for index in range(200):
                now+=rng.choice((30,60,180,300,3600));day=dt.datetime.fromtimestamp(now).date().isoformat()
                if day!=last_day:tokens=requests=0;last_day=day
                active=rng.choice((True,False))
                if active:tokens+=100;requests+=1
                usage={'date':day,'totalTokens':tokens,'requests':requests,'updatedAt':now}
                remaining=round(max(0,min(100,remaining+rng.choice((-2,-1,-.3,0,.1,1.1)))),1)
                history.observe({'remaining':remaining,'resetsAt':reset,'updatedAt':now},usage if index%7 else None,active)
                self.assert_closed(history.view())

    def test_rendered_totals_hover_and_both_segments_match_the_ledger(self):
        labels=[];original=ImageDraw.ImageDraw.text
        def record(draw,xy,text,*args,**kwargs):labels.append(str(text));return original(draw,xy,text,*args,**kwargs)
        source=example()
        with patch.object(ImageDraw.ImageDraw,'text',record):
            image,actions=render_quota_chart.__wrapped__(2,63,source['reset'],source,0,'2026-10-28',None,'prolite')
        self.assertIn('37%',labels);self.assertTrue(any('总37%' in text and '本机35%' in text and '非本机2%' in text for text in labels))
        for text in labels:self.assertNotRegex(text,r'[0-9]+\.[0-9]+%')
        self.assertFalse(any('待定' in text or '未定位' in text or 'unknown' in text or 'undated' in text for text in labels));self.assertEqual(len(actions),7)
        x=22+(264-44)/7/2;baseline=201
        for fraction,color in ((34.8378/2/37,(93,93,89)),((34.8378+2.1622/2)/37,(185,184,179))):
            pixel=image.getpixel((round(x*2),round((baseline-49*fraction)*2)))
            self.assertTrue(all(abs(a-b)<=3 for a,b in zip(pixel,color)),(pixel,color))


if __name__=='__main__':unittest.main()
