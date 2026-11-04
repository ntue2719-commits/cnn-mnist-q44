#!/usr/bin/env python3
"""Offline auditor-contract tests; parent replay is mocked, not XSim proof."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
for d in (HERE,HERE.parents[1]/'scripts',HERE.parents[1]/'v3b/scripts'):
    if str(d) not in sys.path:sys.path.insert(0,str(d))
from pilot400_common import FIELDS, load_plan, verify_manifest,write_csv,sha256
import audit_v4f2_smoke as auditor
import run_full400_batches_v4f as v4f2

ROOT=Path(__file__).resolve().parents[3]


class AuditScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.repo=self.root/'repo';self.repo.mkdir()
        self.results=self.repo/'fi/results';self.results.mkdir(parents=True)
        self.weights=self.repo/'data';self.weights.mkdir()
        self.v4e=self.results/'v4e_release_review_01';self.v4e.mkdir()
        (self.v4e/'v4e_release_review.json').write_text('{}')
        self.smoke=self.results/'v4f2_smoke_01';self.smoke.mkdir()
        self.out=self.results/'v4f2_smoke_audit_01'
        self.plan=load_plan(ROOT/'fi/config/pilot400_targets.csv',
                            ROOT/'fi/config/fi_targets_candidates_v2.csv')
        self.frozen=verify_manifest(ROOT/'fi/scenarios/pilot400.csv',self.plan)
        self.selected=v4f2.select_smoke(self.frozen,self.plan)
        self.pins={'X':'Y'}
        self.rows=[]
        for row in self.selected:
            self.rows.append({**{k:row[k] for k in FIELDS},
                'outcome':'FINAL_OUTPUT_MATCH','injection_verified':'1',
                'temporal_evidence_scope':'V4DB_10_SAMPLE_BASELINE_ELIGIBILITY_ONLY',
                'consumer_proof_scope':'NOT_INDEPENDENTLY_MONITORED_V4F2',
                'log_sha256':'a'*64})
        write_csv(self.smoke/'smoke_validation.csv',v4f2.RUN_COLUMNS,self.rows)
        self.meta={'schema':v4f2.SCHEMA,'status':v4f2.STATUS_COMPLETE,
            'mode':'qualification-smoke','selected_count':8,
            'xsim_runs_committed':8,'input_pins':self.pins,
            'input_signature':{'selected_ids':list(v4f2.SMOKE_IDS),
                'mode':'qualification-smoke','project':'X.xpr','vivado':'vivado.bat',
                'v4e_review_sha256':sha256(self.v4e/'v4e_release_review.json')},
            'ancestry_replayed':True,'records':[
                {'scenario_id':r['scenario_id'],'validation':r} for r in self.rows],
            'builds':[{'build_id':1}],
            'validation_csv_sha256':sha256(self.smoke/'smoke_validation.csv')}
        self.write()

    def write(self):
        (self.smoke/'v4f2_manifest.json').write_text(json.dumps(self.meta))

    def invoke(self):
        with patch.object(auditor.v4f,'check_frozen'),\
             patch.object(auditor,'load_plan',return_value=self.plan),\
             patch.object(auditor,'verify_manifest',return_value=self.frozen),\
             patch.object(auditor.v4f,'select_400'),\
             patch.object(auditor.v4f,'safety_output'),\
             patch.object(auditor.v4f2,'_source_pins',return_value=self.pins),\
             patch.object(auditor.v4f,'verify_v4e_saved',return_value=({},[])),\
             patch.object(auditor.v4f,'replay_v4e_raw'),\
             patch.object(auditor.v4f2,'verify_records'):
            return auditor.audit(self.repo,self.results,self.weights,
                                 self.v4e,self.smoke,self.out)

    def test_good_contract(self):
        result=self.invoke()
        self.assertEqual(result['status'],auditor.STATUS)
        self.assertEqual(result['distinct_scenarios_verified'],8)
        self.assertFalse(result['full400_authorized'])
        self.assertEqual(result['new_xsim_runs_during_audit'],0)

    def test_wrong_status_rejected(self):
        self.meta['status']=v4f2.STATUS_PAUSED
        self.write()
        with self.assertRaises(ValueError):self.invoke()

    def test_forged_ancestry_rejected(self):
        self.meta['ancestry_replayed']=False
        self.write()
        with self.assertRaises(ValueError):self.invoke()

    def test_wrong_v4e_link_rejected(self):
        self.meta['input_signature']['v4e_review_sha256']='f'*64
        self.write()
        with self.assertRaises(ValueError):self.invoke()

    def test_missing_record_rejected(self):
        self.meta['records'].pop()
        self.write()
        with self.assertRaises(ValueError):self.invoke()

    def test_changed_csv_rejected(self):
        (self.smoke/'smoke_validation.csv').write_text('malicious content')
        with self.assertRaises(ValueError):self.invoke()

    def test_existing_audit_output_rejected(self):
        self.out.mkdir()
        with self.assertRaises(ValueError):self.invoke()

    def test_smoke_outside_results_rejected(self):
        other=self.root/'v4f2_elsewhere';other.mkdir()
        with patch.object(auditor.v4f,'check_frozen'):
            with self.assertRaises(ValueError):
                auditor.audit(self.repo,self.results,self.weights,
                              self.v4e,other,self.out)

    def test_own_raw_source_never_changed(self):
        result=self.invoke()
        self.assertEqual(result['smoke_manifest_sha256'],
                         sha256(self.smoke/'v4f2_manifest.json'))

if __name__=='__main__':unittest.main()
