"""Schema6 contract checks; all persistence stays inside temporary directories."""
import copy
import datetime as dt
from decimal import Decimal
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from quota_accounting import apply_credit,display_labels,quota_breakdown,reconcile_cycle
from sidebar_modules import QuotaHistory


def at(day='2026-10-28',clock='12:00:00'):
    return dt.datetime.fromisoformat(day+'T'+clock).timestamp()


def sample(when,remaining=100,reset=None):
    return {'updatedAt':when,'remaining':remaining,'resetsAt':when+604800 if reset is None else reset}


def usage(when,tokens=100,requests=1):
    return {'date':dt.datetime.fromtimestamp(when).date().isoformat(),
            'totalTokens':tokens,'requests':requests,'updatedAt':when}


def legacy(local=33,other=2,total=37):
    return {'reset':at('2026-11-04','09:00:00'),'firstAt':at(clock='10:00:00'),
            'firstRemaining':98,'lastAt':at(),'lastRemaining':100-total,
            'localUsed':local,'otherUsed':other,
            'localDays':{'2026-10-28':{'points':local}},
            'otherDays':{'2026-10-28':{'points':other}},
            'days':{'2026-10-28':{'points':local+other}}}


class TwoSourceLedgerTests(unittest.TestCase):
    def assert_closed(self,cycle,remaining=None,day=None):
        dec=lambda n:Decimal(str(n))
        self.assertFalse({'unknownUsed','unknownDays','unallocated','unknown','undated'}&cycle.keys())
        total=dec(cycle['localUsed'])+dec(cycle['otherUsed'])
        self.assertGreaterEqual(total,0);self.assertLessEqual(total,100)
        if remaining is not None:self.assertEqual(total,Decimal(100)-dec(remaining))
        for aggregate,daily in (('localUsed','localDays'),('otherUsed','otherDays')):
            points=[dec(row['points']) for row in cycle[daily].values()]
            self.assertTrue(all(p>=0 and p==p.quantize(Decimal('.0001')) for p in points))
            self.assertEqual(sum(points,Decimal(0)),dec(cycle[aggregate]))
            self.assertTrue(set(cycle[daily])<=set(cycle['days']))
        for key,row in cycle['days'].items():
            dt.date.fromisoformat(key)
            self.assertEqual(sum(dec(cycle[daily].get(key,{}).get('points',0)) for daily in ('localDays','otherDays')),dec(row['points']))
        self.assertEqual(sum((dec(row['points']) for row in cycle['days'].values()),Decimal(0)),total)
        day=day or dt.datetime.fromtimestamp(cycle['lastAt']).date().isoformat()
        summary=quota_breakdown(cycle,reset=cycle.get('reset'),day_key=day)
        self.assertEqual(set(summary),{'total','local','other','days','ledger','labels'})
        self.assertEqual(dec(summary['total']),total)
        self.assertEqual(dec(summary['local'])+dec(summary['other']),total)
        self.assertEqual(sum((dec(row['total'] or 0) for row in summary['days']),Decimal(0)),total)
        for row in [summary,*summary['days']]:
            self.assertEqual(set(row['labels']),{'total','local','other'})
            for value in row['labels'].values():
                self.assertIsInstance(value,str)
                self.assertRegex(value,r'^[0-9]+\Z')
            self.assertEqual(dec(row['labels']['local'])+dec(row['labels']['other']),dec(row['labels']['total']))
            self.assertEqual(dec(row['local'])+dec(row['other']),dec(row['total'] or 0))
            self.assertFalse({'unknown','undated'}&row.keys())
        for key in ('total','local','other'):
            self.assertEqual(sum((dec(row['labels'][key]) for row in summary['days']),Decimal(0)),dec(summary['labels'][key]))

    def test_no_evidence_first_sample_is_neutral_even_when_active_now(self):
        for active in (False,True):
            with self.subTest(active=active),tempfile.TemporaryDirectory() as directory:
                history=QuotaHistory(Path(directory)/'history.json')
                history.observe(sample(at(),80),local_active=active)
                self.assertEqual((history.view()['localUsed'],history.view()['otherUsed']),(10,10))
                self.assert_closed(history.view(),80)

    def test_no_evidence_delta_uses_existing_smoothed_prior(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'history.json';source=legacy(4,2,6);source['firstRemaining']=94
            path.write_text(json.dumps({'version':5,'cycles':[source]}),encoding='utf-8')
            history=QuotaHistory(path)
            history.observe(sample(source['lastAt']+60,86,source['reset']))
            self.assertEqual((history.view()['localUsed'],history.view()['otherUsed']),(9,5))
            self.assert_closed(history.view(),86)

    def test_explicit_unknown_migration_uses_prior_and_removes_legacy_fields(self):
        source=legacy();source.update(unknownUsed=2,unknownDays={'2026-10-28':{'points':2}},unallocated=0)
        source['days']['2026-10-28']['points']=37
        original=copy.deepcopy(source);cycle=reconcile_cycle(source)
        self.assertEqual((cycle['localUsed'],cycle['otherUsed']),(34.8378,2.1622))
        self.assertEqual(source,original)
        self.assert_closed(cycle,63)
        self.assertEqual(cycle,reconcile_cycle(cycle))

    def test_fully_attributed_35_and_2_survive_repeated_normalization(self):
        source=legacy(35,2,37);source['firstRemaining']=100
        cycle=reconcile_cycle(source)
        for _ in range(20):
            self.assertEqual((cycle['localUsed'],cycle['otherUsed']),(35,2))
            self.assertEqual(cycle['days']['2026-10-28']['points'],37)
            self.assertEqual(cycle,reconcile_cycle(cycle))
        self.assert_closed(cycle,63)

    def test_legacy_missing_dates_fill_the_cycle_without_all_landing_today(self):
        source=legacy(35,2,37);source['firstRemaining']=100
        source['localDays']={};source['otherDays']={};source['days']={}
        source['firstAt']=at('2026-10-27');source['reset']=at('2026-11-03','00:00:00')
        cycle=reconcile_cycle(source)
        self.assertEqual((cycle['localUsed'],cycle['otherUsed']),(35,2))
        self.assertGreater(cycle['days']['2026-10-27']['points'],0)
        self.assertLess(cycle['days']['2026-10-28']['points'],37)
        self.assertTrue(all('2026-10-27'<=key<='2026-10-28' for key in cycle['days']))
        self.assert_closed(cycle,63)

    def test_initial_same_day_gap_is_assigned_to_that_day(self):
        with tempfile.TemporaryDirectory() as directory:
            history=QuotaHistory(Path(directory)/'history.json');start=at(clock='12:00:00')
            history.observe(sample(start,96,at('2026-11-04','00:00:00')))
            self.assertEqual(history.view()['days']['2026-10-28']['points'],4)
            self.assertEqual((history.view()['localUsed'],history.view()['otherUsed']),(2,2))
            self.assert_closed(history.view(),96)

    def test_initial_cross_midnight_gap_uses_seconds_not_equal_day_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            history=QuotaHistory(Path(directory)/'history.json')
            history.observe(sample(at('2026-10-29','00:30:00'),96,at('2026-11-04','23:50:00')))
            cycle=history.view()
            self.assertEqual(cycle['days']['2026-10-28']['points'],1)
            self.assertEqual(cycle['days']['2026-10-29']['points'],3)
            self.assert_closed(cycle,96)

    def test_observed_cross_midnight_interval_splits_each_source_by_seconds(self):
        for active in (False,True):
            with self.subTest(active=active),tempfile.TemporaryDirectory() as directory:
                history=QuotaHistory(Path(directory)/'history.json');start=at(clock='23:58:00');reset=start+604800
                history.observe(sample(start,100,reset))
                history.observe(sample(start+480,92,reset),local_active=active)
                cycle=history.view()
                self.assertEqual(cycle['days']['2026-10-28']['points'],2)
                self.assertEqual(cycle['days']['2026-10-29']['points'],6)
                self.assertEqual(cycle['localDays']['2026-10-28']['points'],2 if active else 1)
                self.assertEqual(cycle['localDays']['2026-10-29']['points'],6 if active else 3)
                self.assert_closed(cycle,92)

    def test_original_bytes_are_backed_up_once_for_every_legacy_schema(self):
        for version in (1,3,4,5):
            with self.subTest(version=version),tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'history.json'
                original=(' {\n "version": '+str(version)+', "note": "原件", "cycles": '+json.dumps([legacy()])+'\n}\n').encode('utf-8')
                path.write_bytes(original);history=QuotaHistory(path)
                self.assertEqual(history.data['version'],6)
                candidates=[path.with_name('history.schema5-backup.json')]
                if version<4:candidates.append(path.with_name('history.schema1-backup.json'))
                elif version==4:candidates.append(path.with_name('history.schema4-backup.json'))
                backups=[p for p in candidates if p.exists()]
                self.assertTrue(backups)
                for backup in backups:self.assertEqual(backup.read_bytes(),original)
                files={p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in Path(directory).iterdir()}
                saved=copy.deepcopy(history.data)
                with patch('sidebar_modules.atomic_json') as write:
                    for _ in range(3):self.assertEqual(QuotaHistory(path).data,saved)
                    write.assert_not_called()
                self.assertEqual({p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in Path(directory).iterdir()},files)
                self.assert_closed(history.view(),63)

    def test_existing_schema5_backup_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'history.json';backup=path.with_name('history.schema5-backup.json')
            backup.write_bytes(b'previous original bytes\r\n')
            path.write_text(json.dumps({'version':5,'cycles':[legacy()]}),encoding='utf-8')
            history=QuotaHistory(path)
            self.assertEqual(backup.read_bytes(),b'previous original bytes\r\n')
            self.assert_closed(history.view(),63)

    def test_active_short_interval_includes_the_900_second_boundary(self):
        for gap in (1,60,899,900):
            with self.subTest(gap=gap),tempfile.TemporaryDirectory() as directory:
                history=QuotaHistory(Path(directory)/'history.json');start=at();reset=start+604800
                history.observe(sample(start,100,reset))
                history.observe(sample(start+gap,97,reset),local_active=True)
                self.assertEqual((history.view()['localUsed'],history.view()['otherUsed']),(3,0))
                self.assert_closed(history.view(),97)

    def test_long_activity_gap_uses_coverage_and_non_neutral_prior(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'history.json';source=legacy(8,2,10);source['firstRemaining']=90
            path.write_text(json.dumps({'version':5,'cycles':[source]}),encoding='utf-8')
            history=QuotaHistory(path)
            history.observe(sample(source['lastAt']+3600,86,source['reset']),local_active=True)
            # prior=9/12, r=1/4: four new points split 3.25 / 0.75.
            self.assertEqual((history.view()['localUsed'],history.view()['otherUsed']),(11.25,2.75))
            self.assert_closed(history.view(),86)

    def test_long_active_gaps_do_not_attribute_the_entire_interval_to_local(self):
        for gap,expected in ((901,9.9945),(3600,6.25),(14400,5.3125),(86400,5.0521)):
            with self.subTest(gap=gap),tempfile.TemporaryDirectory() as directory:
                history=QuotaHistory(Path(directory)/'history.json');start=at();reset=start+604800
                history.observe(sample(start,100,reset))
                history.observe(sample(start+gap,90,reset),local_active=True)
                self.assertEqual(history.view()['localUsed'],expected)
                self.assertGreater(history.view()['otherUsed'],0)
                self.assert_closed(history.view(),90)

    def test_pending_growth_is_consumed_once_then_equal_fresh_counters_mean_other(self):
        with tempfile.TemporaryDirectory() as directory:
            history=QuotaHistory(Path(directory)/'history.json');start=at();reset=start+604800
            history.observe(sample(start,100,reset),usage(start))
            history.observe(sample(start+60,100,reset),usage(start+60,200,2))
            history.observe(sample(start+180,98,reset),usage(start+180,200,2))
            self.assertEqual((history.view()['localUsed'],history.view()['otherUsed']),(2,0))
            history.observe(sample(start+240,97,reset),usage(start+240,200,2))
            self.assertEqual((history.view()['localUsed'],history.view()['otherUsed']),(2,1))
            self.assert_closed(history.view(),97)

    def test_expired_pending_does_not_override_neutral_prior(self):
        with tempfile.TemporaryDirectory() as directory:
            history=QuotaHistory(Path(directory)/'history.json');start=at();reset=start+604800
            history.observe(sample(start,100,reset),usage(start))
            history.observe(sample(start+60,100,reset),usage(start+60,200,2))
            history.observe(sample(start+961,98,reset))
            self.assertEqual((history.view()['localUsed'],history.view()['otherUsed']),(1,1))
            self.assert_closed(history.view(),98)

    def test_stale_and_regressing_local_samples_do_not_claim_local_or_other(self):
        start=at()
        for current in (None,usage(start-10,200,2),usage(start+60,50,0),usage(start-600,200,2),usage(start+60,200,0),usage(start+60,50,2)):
            with self.subTest(current=current),tempfile.TemporaryDirectory() as directory:
                history=QuotaHistory(Path(directory)/'history.json');reset=start+604800
                history.observe(sample(start,100,reset),usage(start))
                history.observe(sample(start+60,99,reset),current)
                self.assertEqual((history.view()['localUsed'],history.view()['otherUsed']),(.5,.5))
                self.assert_closed(history.view(),99)

    def test_expired_regressing_and_same_stamp_quota_samples_never_reapply_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'history.json';history=QuotaHistory(path);start=at();reset=start+604800
            history.observe(sample(start,100,reset));history.observe(sample(start+60,98,reset),local_active=True)
            saved=copy.deepcopy(history.data);saved_bytes=path.read_bytes()
            with patch('sidebar_modules.atomic_json') as write:
                for stale in (sample(start+30,20,reset),sample(reset,20,reset),sample(reset+1,20,reset),sample(start+60,40,reset)):
                    history.observe(stale)
                    self.assertEqual(history.data,saved)
                write.assert_not_called()
            self.assertEqual(path.read_bytes(),saved_bytes)

    def test_new_window_uses_neutral_prior_and_does_not_change_old_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            history=QuotaHistory(Path(directory)/'history.json');start=at();reset=start+604800
            history.observe(sample(start,100,reset));history.observe(sample(start+60,65,reset),local_active=True)
            previous=copy.deepcopy(history.view())
            history.observe(sample(reset+120,98,reset+604800),local_active=True)
            self.assertEqual(len(history.data['cycles']),2)
            self.assertEqual(history.data['cycles'][0],previous)
            self.assertEqual((history.view()['localUsed'],history.view()['otherUsed']),(1,1))
            self.assert_closed(history.view(),98)

    def test_reset_jitter_through_120_seconds_preserves_the_window(self):
        with tempfile.TemporaryDirectory() as directory:
            history=QuotaHistory(Path(directory)/'history.json');start=at();reset=start+604800
            for i,jitter in enumerate((0,120,-120,119,-119,0)):
                history.observe(sample(start+i*60,100-i,reset+jitter),local_active=True)
                self.assertEqual(len(history.data['cycles']),1)
                self.assertEqual(history.view()['firstAt'],start)
                self.assert_closed(history.view(),100-i)
            history.observe(sample(start+360,100,reset+121))
            self.assertEqual(len(history.data['cycles']),2)

    def test_refund_reduces_latest_day_in_proportion_and_leaves_prior_day(self):
        source=legacy(10,6,16);source['firstAt']=at('2026-10-27');source['firstRemaining']=100
        source['reset']=at('2026-11-03','00:00:00')
        source['localDays']={'2026-10-27':{'points':4},'2026-10-28':{'points':6}}
        source['otherDays']={'2026-10-27':{'points':4},'2026-10-28':{'points':2}}
        source['days']={'2026-10-27':{'points':8},'2026-10-28':{'points':8}}
        source=reconcile_cycle(source);before=copy.deepcopy(source)
        apply_credit(source,1.0001);source['lastRemaining']=85.0001
        self.assertEqual(source['localDays']['2026-10-27'],before['localDays']['2026-10-27'])
        self.assertEqual(source['otherDays']['2026-10-27'],before['otherDays']['2026-10-27'])
        self.assertEqual(source['localDays']['2026-10-28']['points'],5.2499)
        self.assertEqual(source['otherDays']['2026-10-28']['points'],1.75)
        self.assert_closed(source,85.0001)

    def test_refund_spills_to_earlier_days_only_after_latest_is_empty(self):
        source=legacy(7,5,12);source['reset']=at('2026-11-03','00:00:00');source['firstAt']=at('2026-10-27');source['firstRemaining']=100
        source['localDays']={'2026-10-27':{'points':6},'2026-10-28':{'points':1}}
        source['otherDays']={'2026-10-27':{'points':2},'2026-10-28':{'points':3}}
        source['days']={'2026-10-27':{'points':8},'2026-10-28':{'points':4}}
        source=reconcile_cycle(source);apply_credit(source,5);source['lastRemaining']=93
        self.assertEqual(source['days']['2026-10-28']['points'],0)
        self.assertEqual(source['localDays']['2026-10-27']['points'],5.25)
        self.assertEqual(source['otherDays']['2026-10-27']['points'],1.75)
        self.assert_closed(source,93)

    def test_projection_of_refund_and_new_window_never_mutates_memory_or_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'history.json';path.write_text(json.dumps({'version':5,'cycles':[legacy()]}),encoding='utf-8')
            history=QuotaHistory(path);original=copy.deepcopy(history.data)
            files={p.name:p.read_bytes() for p in Path(directory).iterdir()}
            for remaining,reset,day in ((63,history.view()['reset'],'2026-10-28'),(65,history.view()['reset'],'2026-10-28'),(98,at('2026-11-05','09:00:00'),'2026-10-29')):
                summary=quota_breakdown(history.view(),remaining,reset,day)
                self.assert_closed(summary['ledger'],remaining,day)
                self.assertEqual(history.data,original)
            self.assertEqual({p.name:p.read_bytes() for p in Path(directory).iterdir()},files)

    def test_display_rounding_closes_with_small_and_nearly_tied_sources(self):
        self.assertEqual(display_labels(37,34.8378,2.1622),{'total':'37','local':'35','other':'2'})
        for total,local,other in ((0,0,0),(.0001,.0001,0),(1,.5,.5),(3,1.499,1.501),(37,34.8378,2.1622),(99.9999,33.3333,66.6666)):
            with self.subTest(total=total):
                labels=display_labels(total,local,other)
                self.assertEqual(set(labels),{'total','local','other'})
                for value in labels.values():
                    self.assertIsInstance(value,str)
                    self.assertRegex(value,r'^[0-9]+\Z')
                self.assertEqual(Decimal(labels['local'])+Decimal(labels['other']),Decimal(labels['total']))
                self.assertLessEqual(abs(Decimal(labels['total'])-Decimal(str(total))),Decimal('.5'))
                self.assertLessEqual(abs(Decimal(labels['local'])-Decimal(str(local))),Decimal('1'))
                self.assertLessEqual(abs(Decimal(labels['other'])-Decimal(str(other))),Decimal('1'))

    def test_tiny_daily_labels_close_across_days_and_both_sources(self):
        source=legacy(.6,.9,1.5);source['reset']=at('2026-11-02','00:00:00')
        source['firstAt']=at('2026-10-26','00:00:00');source['firstRemaining']=100
        keys=('2026-10-26','2026-10-27','2026-10-28')
        source['localDays']={key:{'points':.2} for key in keys}
        source['otherDays']={key:{'points':.3} for key in keys}
        source['days']={key:{'points':.5} for key in keys}
        cycle=reconcile_cycle(source);self.assert_closed(cycle,98.5)
        summary=quota_breakdown(cycle,day_key='2026-10-28')
        self.assertEqual(summary['labels']['total'],'2')
        self.assertEqual([row['total'] for row in summary['days'][:3]],[.5,.5,.5])

    def test_history_observation_and_projection_create_no_timers_or_threads(self):
        with tempfile.TemporaryDirectory() as directory,patch('sidebar_modules.threading.Timer',side_effect=AssertionError('unexpected timer')),patch('sidebar_modules.threading.Thread',side_effect=AssertionError('unexpected thread')):
            history=QuotaHistory(Path(directory)/'history.json');start=at();reset=start+604800
            history.observe(sample(start,98,reset));history.observe(sample(start+60,97,reset),local_active=True)
            self.assert_closed(history.view(),97)
            history.observe(sample(start+120,99,reset));self.assert_closed(history.view(),99)

    def test_seeded_random_observations_refunds_rollovers_and_reloads_conserve(self):
        for seed in (0,7,37,120,900,20261028):
            with self.subTest(seed=seed),tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'history.json';history=QuotaHistory(path)
                rng=random.Random(seed);now=at(clock='23:55:00');reset=now+604800
                remaining=98;tokens=requests=0;previous_day=None
                for i in range(100):
                    now+=rng.choice((1,30,120,899,900,901,3600,21600))
                    day=dt.datetime.fromtimestamp(now).date().isoformat()
                    if now>=reset:reset=now+604800;remaining=100
                    if day!=previous_day:tokens=requests=0;previous_day=day
                    active=rng.choice((False,False,True))
                    if active:tokens+=rng.randint(1,1000);requests+=1
                    remaining=round(max(0,min(100,remaining+rng.choice((-3,-1,-.0001,0,.0001,.3333,2,9)))),4)
                    current=usage(now,tokens,requests) if i%7 else None
                    snapshot=sample(now,remaining,reset+rng.choice((-120,0,120)))
                    # Keep this sample unexpired even when testing reset jitter.
                    if snapshot['resetsAt']<=now:snapshot['resetsAt']=reset
                    history.observe(snapshot,current,active)
                    with self.subTest(seed=seed,step=i):
                        self.assert_closed(history.view(),remaining,day)
                        self.assertEqual(history.view(),reconcile_cycle(history.view()))
                        saved=copy.deepcopy(history.data)
                        history.observe(snapshot,current,active)
                        self.assertEqual(history.data,saved)
                        if i%17==0:
                            with patch('sidebar_modules.atomic_json') as write:
                                loaded=QuotaHistory(path);write.assert_not_called()
                            self.assertEqual(loaded.data,history.data)


    def test_reset_estimate_crossing_midnight_keeps_every_point_in_the_chart(self):
        first=at('2026-10-28','23:59:50')
        reset=at('2026-10-29','00:00:30')+604800
        cycle=reconcile_cycle({'reset':reset,'firstAt':first,'firstRemaining':98,
                               'lastAt':first,'lastRemaining':98})
        result=quota_breakdown(cycle,reset=reset,day_key='2026-10-29')
        self.assertEqual(sum(row['total'] or 0 for row in result['days']),2)
        self.assertEqual(cycle,reconcile_cycle(cycle))
        self.assertEqual(set(cycle['days']),{'2026-10-29'})


if __name__=='__main__':unittest.main()
