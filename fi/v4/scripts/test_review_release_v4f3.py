#!/usr/bin/env python3
"""Adversarial offline tests for V4f3 release-gap review (NOT XSim tests)."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
for path in (HERE,HERE.parents[1]/'scripts',HERE.parents[1]/'v3b/scripts'):
    if str(path) not in sys.path:sys.path.insert(0,str(path))
import review_release_v4f3 as r
from pilot400_common import FIELDS,write_csv,read_csv,sha256,load_plan,verify_manifest
import qualify_full400_v4f as v4f
import run_full400_batches_v4f as v4f2
import audit_v4f2_smoke as v4f2audit

ROOT=Path(__file__).resolve().parents[3]

class ReleaseGapTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.home=Path(t.name);self.repo=self.home/'repo';self.repo.mkdir()
        self.results=self.repo/'fi/results';self.results.mkdir(parents=True)
        self.weights=self.repo/'data';self.weights.mkdir()
        self.v4e=self.results/'v4e_release_review_01';self.v4e.mkdir()
        (self.v4e/'v4e_release_review.json').write_text('{"scope":"test"}')
        self.phase1=self.results/'v4f_ancestry_01';self.phase1.mkdir()
        self.smoke=self.results/'v4f2_smoke_01';self.smoke.mkdir()
        self.smoke_audit=self.results/'v4f2_smoke_audit_01';self.smoke_audit.mkdir()
        self.out=self.results/'v4f3_release_review_01'
        protocol=self.repo/r.PROTOCOL_REL
        protocol.parent.mkdir(parents=True)
        shutil.copyfile(ROOT/r.PROTOCOL_REL,protocol)
        self.plan=load_plan(ROOT/'fi/config/pilot400_targets.csv',
                            ROOT/'fi/config/fi_targets_candidates_v2.csv')
        self.frozen=verify_manifest(ROOT/'fi/scenarios/pilot400.csv',self.plan)
        self.selected=v4f2.select_smoke(self.frozen,self.plan)
        self.fixture=self.repo/'pin.txt';self.fixture.write_text('unchanged')
        self.pins={str(self.fixture):sha256(self.fixture) for _ in (0,)}
        for i in range(31):
            path=self.repo/f'p_{i}.txt';path.write_text(str(i))
            self.pins[str(path)]=sha256(path)
        self.phase1_meta={
            'schema':v4f.SCHEMA,'status':v4f.STATUS_REPLAY,
            'selected_count':400,'strict_v4e_raw_replay_completed':True,
            'vivado_rtl_source_replay_verified':True,'xsim_runs_added':0,
            'v4e_review_sha256':sha256(self.v4e/'v4e_release_review.json'),
            'full400_authorized':False,'full400_runner_qualified':False,
            'full400_executed':False,'scientific_protocol_frozen':False,
            'target_status_modified':False,
            'frozen_manifest_sha256':v4f.FROZEN_HASHES['fi/scenarios/pilot400.csv'],
            'frozen_plan_sha256':v4f.FROZEN_HASHES['fi/config/pilot400_targets.csv'],
            'preflight_input_sha256':self.pins,
        }
        write_csv(self.phase1/'selected_scenarios.csv',FIELDS,self.frozen)
        self.phase1_meta['selected_sha256']=sha256(self.phase1/'selected_scenarios.csv')
        (self.phase1/'analysis_protocol_DRAFT.json').write_text(json.dumps(v4f.PROTOCOL_DRAFT))
        self.phase1_meta['analysis_protocol_draft_sha256']=sha256(self.phase1/'analysis_protocol_DRAFT.json')
        self.save_phase1()
        self.ef=[]
        for row in self.selected:
            d={**row,'outcome':'FINAL_OUTPUT_MATCH','injection_verified':'1',
               'temporal_evidence_scope':'V4DB_10_SAMPLE_BASELINE_ELIGIBILITY_ONLY',
               'consumer_proof_scope':'NOT_INDEPENDENTLY_MONITORED_V4F2',
               'log_sha256':'a'*64}
            self.ef.append(d)
        write_csv(self.smoke/'smoke_validation.csv',v4f2.RUN_COLUMNS,self.ef)
        self.smoke_meta={
            'schema':v4f2.SCHEMA,'status':v4f2.STATUS_COMPLETE,
            'mode':'qualification-smoke','selected_count':8,'xsim_runs_committed':8,
            'ancestry_replayed':True,'input_pins':self.pins,
            'input_signature':{'selected_ids':list(v4f2.SMOKE_IDS),
                'v4e_review_sha256':sha256(self.v4e/'v4e_release_review.json')},
            'builds':[{'build_id':1}],
            'records':[{'scenario_id':x['scenario_id'],'validation':x} for x in self.ef],
            'uncommitted_artifacts_auto_recovered':False,
            'full400_authorized':False,'full400_runner_qualified':False,
            'full400_executed':False,'scientific_protocol_frozen':False,
            'target_status_modified':False,
        }
        self.save_smoke()
        self.audit_meta={
            'schema':v4f2audit.SCHEMA,'status':v4f2audit.STATUS,
            'smoke_manifest_sha256':sha256(self.smoke/'v4f2_manifest.json'),
            'smoke_validation_sha256':sha256(self.smoke/'smoke_validation.csv'),
            'v4e_review_sha256':sha256(self.v4e/'v4e_release_review.json'),
            'scenario_ids':list(v4f2.SMOKE_IDS),'distinct_scenarios_verified':8,
            'new_xsim_runs_during_audit':0,'outcomes':{'FINAL_OUTPUT_MATCH':8},
            'build_count':1,
            'full400_gate':'BLOCKED_INDEPENDENT_G0_G7_APPROVAL_REQUIRED',
            'full400_authorized':False,'full400_runner_qualified':False,
            'scientific_protocol_frozen':False,'target_status_modified':False,
        }
        self.save_audit()

    def save_phase1(self):
        (self.phase1/'v4f_preflight.json').write_text(json.dumps(self.phase1_meta))
    def save_smoke(self):
        self.smoke_meta['validation_csv_sha256']=sha256(self.smoke/'smoke_validation.csv')
        (self.smoke/'v4f2_manifest.json').write_text(json.dumps(self.smoke_meta))
    def save_audit(self):
        (self.smoke_audit/'v4f2_smoke_audit.json').write_text(json.dumps(self.audit_meta))

    def invoke_smoke(self):
        with patch.object(r.v4f2,'_source_pins',return_value=self.pins),\
             patch.object(r.v4f2,'verify_records'):
            return r.inspect_smoke(self.results,self.smoke,self.smoke_audit,
                                   self.v4e,self.frozen,self.plan,self.weights,self.repo)

    def invoke_review(self):
        with patch.object(r.v4f,'safety_output'),\
             patch.object(r.v4f,'check_frozen'),\
             patch.object(r,'load_plan',return_value=self.plan),\
             patch.object(r,'verify_manifest',return_value=self.frozen),\
             patch.object(r.v4f,'check_assets_and_provenance'),\
             patch.object(r.v4f,'verify_v4e_saved',return_value=({},[])),\
             patch.object(r.v4f,'replay_v4e_raw'),\
             patch.object(r,'inspect_smoke',return_value={'audited':8}):
            return r.review(self.repo,self.results,self.weights,self.v4e,
                            self.phase1,self.smoke,self.smoke_audit,self.out)

    def test_valid_science_candidate_is_not_approved(self):
        obj,digest=r.load_protocol(self.repo)
        self.assertEqual(len(digest),64)
        self.assertFalse(obj['approval']['full400_execution_authorized'])
        self.assertEqual(obj['denominators'],v4f.PROTOCOL_DRAFT['denominators'])

    def test_candidate_refuses_pretend_approval(self):
        p=self.repo/r.PROTOCOL_REL
        o=json.loads(p.read_text());o['approval']['operator_signed']=True
        p.write_text(json.dumps(o))
        with self.assertRaises(ValueError):r.load_protocol(self.repo)

    def test_candidate_refuses_changed_outcome_priority(self):
        p=self.repo/r.PROTOCOL_REL;o=json.loads(p.read_text())
        o['outcome_priority']=[*reversed(o['outcome_priority'])]
        p.write_text(json.dumps(o))
        with self.assertRaises(ValueError):r.load_protocol(self.repo)

    def test_candidate_refuses_false_zero_policy(self):
        p=self.repo/r.PROTOCOL_REL;o=json.loads(p.read_text())
        o['missing_value_policy']['not_observed_is_not_zero']=False
        p.write_text(json.dumps(o))
        with self.assertRaises(ValueError):r.load_protocol(self.repo)

    def test_candidate_refuses_nonexposure_from_final_match(self):
        p=self.repo/r.PROTOCOL_REL;o=json.loads(p.read_text())
        o['nonexposure_policy']['final_output_match_does_not_prove_nonexposure']=False
        p.write_text(json.dumps(o))
        with self.assertRaises(ValueError):r.load_protocol(self.repo)

    def test_phase1_valid_review(self):
        self.assertEqual(r.inspect_plan(self.repo,self.results,self.phase1,self.v4e,self.frozen)['selected_count'],400)

    def test_phase1_not_ancestry_replayed(self):
        self.phase1_meta['strict_v4e_raw_replay_completed']=False;self.save_phase1()
        with self.assertRaises(ValueError):r.inspect_plan(self.repo,self.results,self.phase1,self.v4e,self.frozen)

    def test_phase1_duplicate_or_changed_plan(self):
        with (self.phase1/'selected_scenarios.csv').open('a') as f:f.write('DUPLICATE')
        with self.assertRaises(ValueError):r.inspect_plan(self.repo,self.results,self.phase1,self.v4e,self.frozen)

    def test_phase1_missing_input_pin(self):
        self.fixture.unlink()
        with self.assertRaises(ValueError):r.inspect_plan(self.repo,self.results,self.phase1,self.v4e,self.frozen)

    def test_phase1_fake_release_refused(self):
        self.phase1_meta['full400_authorized']=True;self.save_phase1()
        with self.assertRaises(ValueError):r.inspect_plan(self.repo,self.results,self.phase1,self.v4e,self.frozen)

    def test_smoke_valid_cross_links(self):
        out=self.invoke_smoke()
        self.assertEqual(out['scenario_ids'],list(v4f2.SMOKE_IDS))

    def test_smoke_reject_tampered_raw_audit_link(self):
        self.audit_meta['smoke_manifest_sha256']='0'*64;self.save_audit()
        with self.assertRaises(ValueError):self.invoke_smoke()

    def test_smoke_reject_wrong_outcomes(self):
        self.audit_meta['outcomes']={'WRONG_LABEL':8};self.save_audit()
        with self.assertRaises(ValueError):self.invoke_smoke()

    def test_smoke_reject_fake_e3_claim(self):
        self.ef[0]['consumer_proof_scope']='YES'
        write_csv(self.smoke/'smoke_validation.csv',v4f2.RUN_COLUMNS,self.ef)
        self.smoke_meta['records'][0]['validation']=self.ef[0]
        self.save_smoke();self.audit_meta['smoke_manifest_sha256']=sha256(self.smoke/'v4f2_manifest.json')
        self.audit_meta['smoke_validation_sha256']=sha256(self.smoke/'smoke_validation.csv')
        self.save_audit()
        with self.assertRaises(ValueError):self.invoke_smoke()

    def test_smoke_reject_unapproved_full400(self):
        self.smoke_meta['full400_runner_qualified']=True;self.save_smoke()
        with self.assertRaises(ValueError):self.invoke_smoke()

    def test_smoke_reject_wrong_parent_link(self):
        self.smoke_meta['input_signature']['v4e_review_sha256']='f'*64;self.save_smoke()
        with self.assertRaises(ValueError):self.invoke_smoke()

    def test_smoke_reject_missing_committed_records(self):
        self.smoke_meta['records'].pop();self.save_smoke()
        with self.assertRaises(ValueError):self.invoke_smoke()

    def test_integration_review_is_non_authorizing(self):
        out=self.invoke_review()
        self.assertEqual(out['scope']['frozen_plan'],400)
        self.assertEqual(out['scope']['full400_fi_executed'],0)
        self.assertFalse(out['full400_authorized'])
        self.assertFalse(out['full400_runner_qualified'])
        self.assertFalse(out['scientific_protocol_frozen'])
        self.assertEqual(out['gates']['G6'],r.GATE_STATUSES['G6'])
        self.assertEqual(len(read_csv(self.out/'g0_g7_review.csv',('gate','status'))),8)

    def test_integration_duplicate_output_rejected(self):
        self.out.mkdir()
        with self.assertRaises(ValueError):self.invoke_review()

    def test_integration_protected_dir_rejected(self):
        self.out=self.results/'v4e_release_review_01'
        with self.assertRaises(ValueError):self.invoke_review()

    def test_all_gates_present_and_blocked(self):
        self.assertEqual(set(r.GATE_STATUSES),{f'G{i}' for i in range(8)})
        self.assertTrue(any('BLOCKED' in s for s in r.GATE_STATUSES.values()))

    def test_audit_cannot_execute_or_approve(self):
        import subprocess
        script=ROOT/'fi/v4/scripts/review_release_v4f3.py'
        for flag in ('--execute','--approve'):
            with self.subTest(flag=flag):
                completed=subprocess.run([sys.executable,str(script),flag],
                    text=True,capture_output=True,cwd=ROOT)
                self.assertNotEqual(completed.returncode,0)
                self.assertIn('cannot execute or authorize Full400',completed.stderr)

if __name__=='__main__':unittest.main()
