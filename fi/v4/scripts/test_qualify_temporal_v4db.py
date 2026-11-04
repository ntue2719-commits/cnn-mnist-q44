"""Offline V4d-B temporal sweep regression and adversarial parser tests.

All event traces are synthetic. Only Windows Vivado/XSim creates real evidence.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import qualify_temporal_v4db as m
from pilot400_common import load_plan, verify_manifest, sha256, write_csv

ROOT = Path(__file__).resolve().parents[3]


def sources():
    plan=load_plan(ROOT/'fi/config/pilot400_targets.csv',
                   ROOT/'fi/config/fi_targets_candidates_v2.csv')
    manifest=verify_manifest(ROOT/'fi/scenarios/pilot400.csv',plan)
    return plan,manifest


def fake_events(plan):
    events=set()
    for p in plan:
        tid, cls = p['target_id'],p['target_class']
        a,b=int(p['offset_a']),int(p['offset_b'])
        small,large=min(a,b),max(a,b)
        if cls=='CTL':
            events.update({('ACTIVE',tid,a),('ACTIVE',tid,b)})
        elif cls=='DAT':
            events.add(('PRODUCE',tid,small-3))
            events.update({('CONSUME',tid,a),('CONSUME',tid,b)})
        elif cls=='BUF':
            events.update({('WRITE',tid,small-3),('CONSUME',tid,large+3)})
        else:
            events.update({('WRITE',tid,small-3),('READ',tid,large+3),
                           ('CONSUME',tid,large+4)})
    return sorted(events,key=lambda r:(r[2],r[1],r[0]))


def fake_log(plan, prediction='00', *, profile=True, complete=True):
    lines=['FI_DISABLED_CONTROL_PASS latency=1342 pool1=676 pool2=200 logits=10 prediction='+
           str(int(prediction,16))]
    if profile:
        lines.append('FI_PROFILE_SCHEMA_V2: ACTIVE PRODUCE CONSUME PRE_NBA')
    for kind,tid,offset in fake_events(plan):
        lines.append(f'FI_PROFILE_EVENT,{kind},{tid},{offset+100},{offset}')
    if complete:
        lines.append('FI_PROFILE_PASS: baseline completed, no fault injected')
    return '\n'.join(lines)+'\n'


class ScenarioSelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan,cls.rows=sources()
        cls.anchors=m.selection(cls.rows)

    def test_ten_and_one_per_sample(self):
        self.assertEqual(len(self.anchors),10)
        self.assertEqual([r['sample_id'] for r in self.anchors],
                         [sid for sid,_ in m.SAMPLES])

    def test_anchor_identity(self):
        self.assertEqual({(r['target_id'],r['bit_index'],r['injection_offset'])
                         for r in self.anchors},{('CTL_0001','2','125')})

    def test_anchor_scenarios_unique(self):
        self.assertEqual(len(set(r['scenario_id'] for r in self.anchors)),10)

    def test_no_missing_anchor(self):
        filtered=[r for r in self.rows if r['scenario_id']!=self.anchors[4]['scenario_id']]
        with self.assertRaisesRegex(ValueError,'missing/duplicated|bit2@125'):
            m.selection(filtered)

    def test_no_corrupted_anchor(self):
        altered=deepcopy(self.rows)
        next(r for r in altered if r['scenario_id']==self.anchors[0]['scenario_id'])['injection_offset']='126'
        with self.assertRaisesRegex(ValueError,'bit2@125'):
            m.selection(altered)

    def test_profile_mode_explicit(self):
        from pilot400_direct_xsim import make_cmd_run
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            cmd=make_cmd_run(p,p,self.anchors[0],p/'IMG0000',p/'run.tcl',p/'log',p/'console',
                             profile_only=True)
            self.assertIn('-testplusarg "PILOT_PROFILE_ONLY=1"',cmd)
            self.assertIn('-testplusarg "FI_OFFSET=125"',cmd)
            self.assertIn('IMG0000/input.hex',cmd)
            self.assertNotIn('FI_APPLIED',cmd)

    def test_full400_not_authorized(self):
        self.assertIn('BLOCKED',m.STATUS)
        self.assertIn('BLOCKED',m.FULL400_GATE)
        self.assertEqual(m.WINDOW_COUNT,400)
        self.assertNotIn('fault injection',m.SCHEMA.lower())


class ProfileParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan,cls.rows=sources()
        cls.anchor=m.selection(cls.rows)[0]
        cls.fixture=fake_log(cls.plan,prediction='00')

    def verify(self,text,sample=None):
        with tempfile.TemporaryDirectory() as d:
            temp=Path(d)
            log=temp/'profile.log'; log.write_text(text,encoding='utf-8')
            directory=temp/'asset'; directory.mkdir()
            (directory/'prediction.hex').write_text('00\n',encoding='ascii')
            anchor=dict(self.anchor)
            if sample is not None:
                anchor['sample_id']=sample
            return m.parse_profile_evidence(log,anchor,self.plan,directory)

    def test_all_windows_per_sample(self):
        summary, rows=self.verify(self.fixture)
        self.assertEqual(summary['eligible_windows'],'40')
        self.assertEqual(len(rows),20)
        self.assertEqual(Counter(r['class'] for r in rows),
                         {'CTL':5,'DAT':5,'BUF':5,'MEM':5})
        self.assertTrue(all(r['sample_id']=='IMG0000' for r in rows))
        self.assertEqual(len(summary['event_trace_sha256']),64)

    def test_equal_offset_dat0010_bitpair(self):
        _,rows=self.verify(self.fixture)
        dat=next(r for r in rows if r['target_id']=='DAT_0010')
        self.assertEqual((dat['offset_a'],dat['offset_b']),('1340','1340'))
        self.assertEqual((dat['bit_index_a'],dat['bit_index_b']),('0','1'))
        self.assertEqual(dat['status_a'],dat['status_b'])

    def test_missing_v2_marker(self):
        bad=self.fixture.replace('FI_PROFILE_SCHEMA_V2: ACTIVE PRODUCE CONSUME PRE_NBA\n','')
        with self.assertRaisesRegex(ValueError,'V2 profile'):
            self.verify(bad)

    def test_duplicated_marker(self):
        with self.assertRaisesRegex(ValueError,'V2 profile'):
            self.verify(self.fixture+'FI_PROFILE_SCHEMA_V2: ACTIVE PRODUCE CONSUME PRE_NBA\n')

    def test_fault_injection_rejected(self):
        with self.assertRaisesRegex(ValueError,'injection'):
            self.verify(self.fixture+'FI_APPLIED scenario=x target=y\n')

    def test_fi_result_rejected(self):
        with self.assertRaisesRegex(ValueError,'injection'):
            self.verify(self.fixture+'FI_RESULT_V2,test\n')

    def test_missing_profile_pass(self):
        with self.assertRaisesRegex(ValueError,'one completed'):
            self.verify(self.fixture.replace('FI_PROFILE_PASS: baseline completed, no fault injected\n',''))

    def test_missing_control_summary(self):
        with self.assertRaisesRegex(ValueError,'precise fault-free'):
            self.verify(self.fixture.replace('FI_DISABLED_CONTROL_PASS','FI_DISABLED_CONTROL_FAIL'))

    def test_duplicate_control(self):
        summary=next(line for line in self.fixture.splitlines() if line.startswith('FI_DISABLED_CONTROL_PASS'))
        with self.assertRaisesRegex(ValueError,'precise fault-free'):
            self.verify(self.fixture+summary+'\n')

    def test_wrong_golden_prediction(self):
        with self.assertRaisesRegex(ValueError,'prediction/latency'):
            self.verify(self.fixture.replace('prediction=0','prediction=5'))

    def test_wrong_pool2_count(self):
        with self.assertRaisesRegex(ValueError,'prediction/latency'):
            self.verify(self.fixture.replace('pool2=200','pool2=199'))

    def test_error_line_rejected(self):
        with self.assertRaisesRegex(ValueError,'XSim V2'):
            self.verify(self.fixture+'ERROR: XSim compile failed\n')

    def test_missing_consumer_rejected(self):
        lines=[s for s in self.fixture.splitlines() if not
               (s.startswith('FI_PROFILE_EVENT,CONSUME,DAT_0010,') and s.endswith(',1340'))]
        with self.assertRaisesRegex(ValueError,'temporal eligibility FAILED'):
            self.verify('\n'.join(lines)+'\n')

    def test_wrong_sample_reflected_in_summary(self):
        summary,rows=self.verify(self.fixture,sample='IMG0009')
        self.assertEqual(summary['sample_id'],'IMG0009')
        self.assertTrue(all(r['sample_id']=='IMG0009' for r in rows))

    def test_wrong_event_time_origin(self):
        entry=next(s for s in self.fixture.splitlines() if s.startswith('FI_PROFILE_EVENT,'))
        parts=entry.split(','); parts[3]=str(int(parts[3])+1)
        with self.assertRaisesRegex(ValueError,'Inconsistent'):
            self.verify(self.fixture.replace(entry,','.join(parts)))

    def test_duplicate_event(self):
        entry=next(s for s in self.fixture.splitlines() if s.startswith('FI_PROFILE_EVENT,'))
        with self.assertRaisesRegex(ValueError,'Duplicate event'):
            self.verify(self.fixture+entry+'\n')


class EvidenceLocks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan,cls.rows=sources()
        cls.anchors=m.selection(cls.rows)

    def test_pins_reject_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'source.v'; p.write_text('version 1')
            pins={str(p):sha256(p)}
            m.v4d.check_input_lock(pins)
            p.write_text('version 2')
            with self.assertRaisesRegex(ValueError,'input changed'):
                m.v4d.check_input_lock(pins)

    def test_resume_reject_orphan_log(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); runs=root/'runs'; runs.mkdir()
            (runs/'IMG0000.log').write_text('partial')
            with self.assertRaisesRegex(ValueError,'partial or unrecorded'):
                m.verify_existing_records(root,self.anchors,[],{'fake':'sha'},self.plan,ROOT)

    def test_resume_reject_out_of_order(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'contiguous'):
                m.verify_existing_records(Path(d),self.anchors,
                    [{'sample_id':'IMG0001'}],{'foo':'x'},self.plan,ROOT)

    def test_resume_reject_missing_hash(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'missing/mutated'):
                m.verify_existing_records(Path(d),self.anchors,
                    [{'sample_id':'IMG0000'}],{'foo':'x'},self.plan,ROOT)

    def test_canary_requires_finished_status(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); (p/'v4d_canary_manifest.json').write_text(json.dumps({
                'schema':m.v4d.SCHEMA,'status':'RUNNING','full400_authorized':False,
                'full400_runner_qualified':False,'target_status_modified':False,
                'run_scope':'V4D_A_16_CANARY_ONLY','records':[],'builds':[]}))
            with patch.object(m.v4d,'replay_v4c'), self.assertRaisesRegex(ValueError,'completed'):
                m.verify_canary(ROOT,ROOT,ROOT,ROOT/'x',ROOT/'v',ROOT/'a',ROOT/'b',
                                ROOT/'c',p,self.plan,self.rows)

    def test_report_disclaims_fault_exposure(self):
        report=m.report_text({'status':m.STATUS},[])
        self.assertIn('NOT 400 fault injections',report)
        self.assertIn('BLOCKED',report)

    def test_report_detects_equal_and_divergent_event_traces(self):
        def one(i,digest):
            return {'sample_id':f'IMG{i:04d}','event_count':'100',
                    'eligible_windows':'40','log_sha256':'a'*64,
                    'event_trace_sha256':digest}
        matching=[one(i,'a') for i in range(10)]
        diverse=matching[:-1]+[one(9,'b')]
        self.assertIn('**True**',m.report_text({'status':m.STATUS},matching))
        self.assertIn('**False**',m.report_text({'status':m.STATUS},diverse))

    def test_source_prj_provenance_checked(self):
        code=(m.HERE/'qualify_temporal_v4db.py').read_text()
        self.assertIn("'source_vlog.prj'",code)
        self.assertIn('source_hashes',code)
        self.assertIn('build_verified_snapshot',code)

    def test_no_scenario_outputs_fabricated(self):
        code=(m.HERE/'qualify_temporal_v4db.py').read_text()
        self.assertNotIn('FI_APPLIED scenario=',code)
        self.assertIn('profile_only=True',code)
        self.assertIn('full400_authorized\':False',code)


if __name__=='__main__':
    unittest.main()
