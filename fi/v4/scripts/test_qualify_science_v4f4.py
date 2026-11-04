#!/usr/bin/env python3
"""Fail-closed tests for V4f Phase 4A scientific protocol preparation/sealing."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import qualify_science_v4f4 as m
import review_release_v4f3 as v4f3
import qualify_full400_v4f as v4f

ROOT = Path(__file__).resolve().parents[3]


class ProtocolTruthTable(unittest.TestCase):
    def test_all_five_outcomes_no_stage_measurements(self):
        expected = {
            'NONCOMPLETION': ('TRUE','TRUE','TRUE'),
            'PROTOCOL_VIOLATION': ('TRUE','TRUE','TRUE'),
            'WRONG_LABEL': ('TRUE','TRUE','TRUE'),
            'WRONG_LOGITS_SAME_LABEL': ('FALSE','TRUE','TRUE'),
            'FINAL_OUTPUT_MATCH': ('FALSE','FALSE','INCONCLUSIVE'),
        }
        for outcome, levels in expected.items():
            with self.subTest(outcome=outcome):
                x = m.level_eval(outcome, {'POOL1':'NOT_OBSERVED','POOL2':'INCONCLUSIVE'})
                self.assertEqual((x['L0'],x['L1'],x['L2']), levels)

    def test_wrong_logits_not_harmful(self):
        result = m.level_eval('WRONG_LOGITS_SAME_LABEL',{'POOL1':'MATCH','POOL2':'MATCH'})
        self.assertEqual(result['L0'],'FALSE')
        self.assertEqual(result['L1_over_L0'],'TRUE')
        self.assertEqual(result['L2_over_L1'],'FALSE')

    def test_final_match_propagation_not_misclassified(self):
        result = m.level_eval('FINAL_OUTPUT_MATCH',{'POOL1':'MISMATCH','POOL2':'NOT_OBSERVED'})
        self.assertEqual(result['L0'],'FALSE')
        self.assertEqual(result['L1'],'FALSE')
        self.assertEqual(result['L2'],'TRUE')
        self.assertEqual(result['L2_over_L1'],'TRUE')

    def test_l2_false_requires_both_measured(self):
        x=m.level_eval('FINAL_OUTPUT_MATCH',{'POOL1':'MATCH','POOL2':'MATCH'})
        self.assertEqual(x['L2'],'FALSE')
        self.assertEqual(x['L2_over_L1'],'FALSE')

    def test_l2_missing_not_zero(self):
        for value in ('NOT_OBSERVED','NOT_APPLICABLE','INCONCLUSIVE'):
            with self.subTest(value=value):
                x=m.level_eval('FINAL_OUTPUT_MATCH',{'POOL1':'MATCH','POOL2':value})
                self.assertEqual(x['L2'],'INCONCLUSIVE')
                self.assertEqual(x['L2_over_L1'],'INCONCLUSIVE')

    def test_invalid_stage_keys_rejected(self):
        for stages in ({'POOL1':'MATCH'}, {'POOL1':'MATCH','POOL2':'MATCH','CONV3':'MATCH'},
                       {'POOL1':'MATCH','POOL2':False}, {'POOL1':'MATCH','POOL2':'ZERO'}):
            with self.subTest(stages=stages), self.assertRaises(ValueError):
                m.level_eval('FINAL_OUTPUT_MATCH', stages)

    def test_unknown_outcome_rejected(self):
        for outcome in ('TIMEOUT', 'WRONG_PREDICTION', '', 'INVALID_EVIDENCE'):
            with self.subTest(outcome=outcome), self.assertRaises(ValueError):
                m.level_eval(outcome, {'POOL1':'MATCH','POOL2':'MATCH'})

    def test_increasing_levels_on_known_values(self):
        for outcome in m.OUTCOMES:
            for a in m.STAGE_VALUES:
                for b in m.STAGE_VALUES:
                    levels = m.level_eval(outcome,{'POOL1':a,'POOL2':b})
                    if levels['L0'] == 'TRUE':
                        self.assertEqual(levels['L1'],'TRUE')
                    if levels['L1'] == 'TRUE':
                        self.assertEqual(levels['L2'],'TRUE')


class FreezeArtifactTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)/'repo'
        self.results = self.repo/'fi/results'
        self.results.mkdir(parents=True)
        for name in v4f.FROZEN_HASHES:
            dest=self.repo/name
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/name,dest)
        dest=self.repo/v4f3.PROTOCOL_REL
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/v4f3.PROTOCOL_REL,dest)
        self.review = self.results/'v4f3_release_review_01'
        self.review.mkdir()
        self.v4f3_record={
            'schema':v4f3.SCHEMA,
            'status':v4f3.STATUS,
            'scientific_protocol_candidate_sha256':m.CANDIDATE_SHA256,
            'scientific_protocol_status':'CANDIDATE_NOT_FROZEN_NOT_APPROVED',
            'source_authority':'vivado','new_xsim_runs_during_review':0,
            'scientific_protocol_frozen':False,
            'full400_runner_qualified':False,'full400_authorized':False,
            'full400_executed':False,'target_status_modified':False,
            'original_frozen_files_modified':False,
            'scope':{'frozen_plan':400,'full400_fi_executed':0},
            'gates':dict(v4f3.GATE_STATUSES),
        }
        self.save_review()
        shutil.copyfile(dest,self.review/'scientific_protocol_candidate.json')
        with (self.review/'g0_g7_review.csv').open('w',newline='',encoding='utf-8') as handle:
            writer=csv.DictWriter(handle,fieldnames=('gate','status'),lineterminator='\n')
            writer.writeheader()
            writer.writerows([{'gate':key,'status':value}
                              for key,value in v4f3.GATE_STATUSES.items()])
        self.prepared=self.results/'v4f4_prepared_01'
        self.sealed=self.results/'v4f4_sealed_01'
        self.operator_file=self.repo/'reviews/operator.json'
        self.reviewer_file=self.repo/'reviews/independent.json'
        self.operator_file.parent.mkdir(parents=True,exist_ok=True)

    def save_review(self):
        (self.review/'v4f3_release_review.json').write_bytes(m.json_bytes(self.v4f3_record))

    def prepare(self):
        return m.prepare(self.repo,self.review,self.prepared)

    def attestation(self,role,name):
        template=m.strict_json(self.prepared/('TEMPLATE_'+role+'.json'))
        template['reviewer_id']=name
        template['decision']=m.APPROVE_DECISION
        template['reviewed_utc']=datetime.now(timezone.utc).isoformat()
        template['checklist']={key:True for key in m.CHECKS}
        return template

    def write_receipt(self,path,data):
        path.write_bytes(m.json_bytes(data))

    def ready_receipts(self):
        self.prepare()
        operator=self.attestation('operator','operator_name')
        reviewer=self.attestation('independent_reviewer','external_reviewer_name')
        self.write_receipt(self.operator_file,operator)
        self.write_receipt(self.reviewer_file,reviewer)
        return operator,reviewer

    def seal(self):
        return m.seal(self.repo,self.review,self.prepared,self.sealed,
                      self.operator_file,self.reviewer_file)

    def test_real_candidate_sha_pinned(self):
        candidate,digest=m.load_candidate(ROOT)
        self.assertEqual(digest,m.CANDIDATE_SHA256)
        self.assertEqual(candidate['design']['planned_scenarios'],400)

    def test_prepare_success_pending_only(self):
        out=self.prepare()
        self.assertFalse(out['full400_authorized'])
        self.assertEqual(out['G7'],'OPEN_AWAITING_TWO_SEPARATE_MANUAL_ATTESTATIONS')
        core=m.strict_json(self.prepared/'protocol_core.json')
        self.assertEqual(core['schema'],m.SCHEMA_CORE)
        self.assertEqual(m.sha256(self.prepared/'protocol_core.json'),out['core_sha256'])
        self.assertEqual(m.check_prepared(self.repo,self.review,self.prepared)[0],out)

    def test_prepared_attestations_default_pending(self):
        self.prepare()
        for role in ('operator','independent_reviewer'):
            att=m.strict_json(self.prepared/('TEMPLATE_'+role+'.json'))
            self.assertEqual(att['decision'],'PENDING')
            self.assertFalse(any(att['checklist'].values()))

    def test_prepared_reject_overwrite(self):
        self.prepare()
        with self.assertRaises(ValueError): self.prepare()

    def test_prepared_reject_wrong_parent(self):
        out=self.repo/'fi/results/v4e_release_review_01'
        with self.assertRaises(ValueError):m.prepare(self.repo,self.review,out)

    def test_prepared_reject_fake_release_flag(self):
        self.v4f3_record['full400_authorized']=True
        self.save_review()
        with self.assertRaises(ValueError):self.prepare()

    def test_prepared_reject_fake_g6(self):
        self.v4f3_record['gates']['G6']='PASS'
        self.save_review()
        with self.assertRaises(ValueError):self.prepare()

    def test_prepared_reject_candidate_tamper(self):
        candidate=self.repo/v4f3.PROTOCOL_REL
        candidate.write_text(candidate.read_text()+'\n')
        with self.assertRaises(ValueError):self.prepare()

    def test_prepared_reject_frozen_file_tamper(self):
        path=self.repo/'fi/scenarios/pilot400.csv'
        with path.open('ab') as handle:handle.write(b'\n')
        with self.assertRaises(ValueError):self.prepare()

    def test_prepared_reject_candidate_mirror_tamper(self):
        path=self.review/'scientific_protocol_candidate.json'
        obj=m.strict_json(path)
        obj['approval']['operator_signed']=True
        path.write_bytes(m.json_bytes(obj))
        with self.assertRaises(ValueError):self.prepare()

    def test_prepared_reject_gates_csv_tamper(self):
        path=self.review/'g0_g7_review.csv'
        path.write_text('gate,status\nG0,PASS\n')
        with self.assertRaises(ValueError):self.prepare()

    def test_seal_two_explicit_manual_receipts(self):
        self.ready_receipts()
        out=self.seal()
        self.assertFalse(out['full400_authorized'])
        self.assertEqual(out['G0_G7_final_release'],'NOT_GRANTED')
        self.assertEqual(m.verify_seal(self.repo,self.review,self.prepared,self.sealed),out)

    def test_seal_reject_duplicate_reviewer(self):
        self.ready_receipts()
        o=m.strict_json(self.operator_file); r=m.strict_json(self.reviewer_file)
        r['reviewer_id']=o['reviewer_id']
        self.write_receipt(self.reviewer_file,r)
        with self.assertRaises(ValueError):self.seal()

    def test_seal_reject_approval_missing(self):
        self.ready_receipts()
        data=m.strict_json(self.operator_file)
        data['decision']='PENDING'
        self.write_receipt(self.operator_file,data)
        with self.assertRaises(ValueError):self.seal()

    def test_seal_reject_checklist_false(self):
        self.ready_receipts()
        data=m.strict_json(self.reviewer_file)
        data['checklist']['missingness_and_censoring']=False
        self.write_receipt(self.reviewer_file,data)
        with self.assertRaises(ValueError):self.seal()

    def test_seal_reject_wrong_protocol_sha(self):
        self.ready_receipts()
        data=m.strict_json(self.reviewer_file)
        data['protocol_sha256']='f'*64
        self.write_receipt(self.reviewer_file,data)
        with self.assertRaises(ValueError):self.seal()

    def test_seal_reject_wrong_review_sha(self):
        self.ready_receipts()
        data=m.strict_json(self.reviewer_file)
        data['v4f3_review_sha256']='0'*64
        self.write_receipt(self.reviewer_file,data)
        with self.assertRaises(ValueError):self.seal()

    def test_seal_reject_fake_utc(self):
        self.ready_receipts()
        data=m.strict_json(self.operator_file)
        data['reviewed_utc']='2026-11-04T00:00:00'
        self.write_receipt(self.operator_file,data)
        with self.assertRaises(ValueError):self.seal()

    def test_seal_reject_prepared_tamper(self):
        self.ready_receipts()
        path=self.prepared/'protocol_core.json'
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaises(ValueError):self.seal()

    def test_seal_reject_prepared_template_tamper(self):
        self.ready_receipts()
        path=self.prepared/'TEMPLATE_operator.json'
        path.write_bytes(path.read_bytes()+b' ')
        # The verifier checks semantic + template checksum, but extra whitespace
        # isn't content change if parser normalizes. It must reject changed bytes.
        with self.assertRaises(ValueError):self.seal()

    def test_seal_reject_receipts_in_prepared_evidence(self):
        self.prepare()
        p=self.prepared/'fake_operator.json'
        q=self.prepared/'fake_reviewer.json'
        self.write_receipt(p,self.attestation('operator','op'))
        self.write_receipt(q,self.attestation('independent_reviewer','rev'))
        with self.assertRaises(ValueError):
            m.seal(self.repo,self.review,self.prepared,self.sealed,p,q)

    def test_seal_reject_duplicate_json_keys(self):
        self.ready_receipts()
        text=self.operator_file.read_text()
        self.operator_file.write_text(text.replace('"role": "operator",',
                                      '"role": "operator", "role": "operator",'))
        with self.assertRaises(ValueError):self.seal()

    def test_seal_reject_output_overwrite(self):
        self.ready_receipts();self.seal()
        with self.assertRaises(ValueError):self.seal()

    def test_verify_detects_tampered_seal_record(self):
        self.ready_receipts();self.seal()
        path=self.sealed/'v4f4_seal.json'
        obj=m.strict_json(path);obj['full400_authorized']=True
        path.write_bytes(m.json_bytes(obj))
        with self.assertRaises(ValueError):
            m.verify_seal(self.repo,self.review,self.prepared,self.sealed)

    def test_verify_detects_tampered_seal_receipt(self):
        self.ready_receipts();self.seal()
        path=self.sealed/'operator_attestation.json'
        obj=m.strict_json(path);obj['reviewer_id']='new_person'
        path.write_bytes(m.json_bytes(obj))
        with self.assertRaises(ValueError):
            m.verify_seal(self.repo,self.review,self.prepared,self.sealed)

    def test_verify_detects_changed_receipt_whitespace(self):
        self.ready_receipts();self.seal()
        path=self.sealed/'operator_attestation.json'
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaises(ValueError):
            m.verify_seal(self.repo,self.review,self.prepared,self.sealed)

    def test_cli_end_to_end_preparation_seal_and_verify(self):
        script=HERE/'qualify_science_v4f4.py'
        common=[sys.executable,str(script),'--repo',str(self.repo),
                '--v4f3-dir','fi/results/v4f3_release_review_01']
        planned=subprocess.run(common+['--mode','prepare','--out-dir',
                     'fi/results/v4f4_prepared_01'],capture_output=True,text=True)
        self.assertEqual(planned.returncode,0,planned.stderr)
        self.assertIn('V4F4_G7_PREPARE_PASS',planned.stdout)
        self.write_receipt(self.operator_file,
                           self.attestation('operator','operator_name'))
        self.write_receipt(self.reviewer_file,
                           self.attestation('independent_reviewer','independent_name'))
        issued=subprocess.run(common+['--mode','seal','--prepared-dir',
                 'fi/results/v4f4_prepared_01',
                 '--operator-file',str(self.operator_file),
                 '--independent-file',str(self.reviewer_file),
                 '--out-dir','fi/results/v4f4_sealed_01'],
                 capture_output=True,text=True)
        self.assertEqual(issued.returncode,0,issued.stderr)
        self.assertIn('V4F4_PROTOCOL_MANUAL_SEAL_PASS',issued.stdout)
        verified=subprocess.run(common+['--mode','verify','--prepared-dir',
                 'fi/results/v4f4_prepared_01','--sealed-dir',
                 'fi/results/v4f4_sealed_01'],capture_output=True,text=True)
        self.assertEqual(verified.returncode,0,verified.stderr)
        self.assertIn('V4F4_PROTOCOL_SEAL_VERIFY_PASS',verified.stdout)

    def test_cli_rejects_execution_or_full400_release(self):
        script=HERE/'qualify_science_v4f4.py'
        for flag in ('--execute','--authorize-full400'):
            with self.subTest(flag=flag):
                proc=subprocess.run([sys.executable,str(script),'--mode','prepare',flag],
                                    cwd=ROOT,capture_output=True,text=True)
                self.assertNotEqual(proc.returncode,0)
                self.assertIn('never executes/authorizes Full400',proc.stderr)


if __name__=='__main__':
    unittest.main()
