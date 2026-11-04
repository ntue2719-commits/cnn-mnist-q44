#!/usr/bin/env python3
"""Static V3a proof-contract tests; no claim that XSim ran here."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from pilot400_common import load_plan, verify_manifest
from pilot400_direct_xsim import make_cmd_run
from run_pilot400_target_validation_v3 import (
    APPLIED_RE, REPORT_FIELDS, candidate_targets, select_scenarios,
    inspect_applied_and_result, summarize_targets, evidence_md)

REPO = Path(__file__).resolve().parents[2]


def fake_fi_log(row, *, old='0', new='1', outcome='FINAL_OUTPUT_MATCH',
                pool1='0', pool2='0', logits='0', extra=''):
    fi_offset=int(row['injection_offset'])
    fields=[row['scenario_id'], row['sample_id'], row['target_id'],
            row['target_class'],row['bit_index'],str(fi_offset),
            str(fi_offset+3),str(fi_offset+3),'1','1',
            '1345','784','784','784','169','25','1','1','1','1',
            pool1,pool2,logits,'0',
            ('POOL1' if int(pool1)>0 else ('FC' if int(logits)>0 else 'NONE')),
            outcome]
    return ('FI_DISABLED_CONTROL_PASS latency=1342 pool1=676 pool2=200 logits=10 prediction=0\n'
            f'FI_APPLIED scenario={row["scenario_id"]} target={row["target_id"]} '
            f'class={row["target_class"]} bit={row["bit_index"]} old={old} new={new} '
            f'requested={fi_offset+3}\n'
            'FI_RESULT_V2,'+','.join(fields)+'\n'
            'CNN_PEP FI CAMPAIGN V2: PASS (mechanism/log integrity; outcome may be harmful)\n'
            +extra)


class TargetV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan=load_plan(REPO/'fi/config/pilot400_targets.csv',
                           REPO/'fi/config/fi_targets_candidates_v2.csv')
        cls.manifest=verify_manifest(REPO/'fi/scenarios/pilot400.csv',cls.plan)
        cls.targets=candidate_targets(cls.plan)
        cls.rows=select_scenarios(cls.manifest,cls.targets,('IMG0000',))

    def test_exact_14_pending_sites(self):
        self.assertEqual(len(self.targets),14)
        self.assertEqual([sum(p['target_class']==cls for p in self.targets)
                          for cls in ('CTL','BUF','MEM','DAT')],[4,3,3,4])

    def test_two_configurations_per_candidate(self):
        self.assertEqual(len(self.rows),28)
        self.assertEqual(len(set(x['scenario_id'] for x in self.rows)),28)
        for p in self.targets:
            rr=[r for r in self.rows if r['target_id']==p['target_id']]
            self.assertEqual(len(rr),2)
            self.assertEqual({(r['bit_index'],r['injection_offset']) for r in rr},
                             {(p['bit_index'],p['offset_a']),
                              (p['bit_index_b'],p['offset_b'])})

    def test_optional_two_image_subset(self):
        t=candidate_targets(self.plan,('MEM_0004',))
        rows=select_scenarios(self.manifest,t,('IMG0000','IMG0001'))
        self.assertEqual(len(rows),4)
        self.assertEqual({r['sample_id'] for r in rows},{'IMG0000','IMG0001'})

    def test_reject_non_candidate_target(self):
        with self.assertRaises(ValueError):
            candidate_targets(self.plan,('DAT_0010',))

    def test_reject_duplicate_requested_target(self):
        with self.assertRaises(ValueError):
            candidate_targets(self.plan,('MEM_0004','MEM_0004'))

    def test_reject_duplicate_samples(self):
        with self.assertRaises(ValueError):
            select_scenarios(self.manifest,self.targets,('IMG0000','IMG0000'))

    def test_reject_mutated_manifest_pair(self):
        corrupt=[r.copy() for r in self.manifest]
        for row in corrupt:
            if row['target_id']=='MEM_0004' and row['sample_id']=='IMG0000':
                row['bit_index']='1'
                break
        with self.assertRaises(ValueError):
            select_scenarios(corrupt,self.targets,('IMG0000',))

    def test_direct_fi_command_is_not_profile_only(self):
        row=self.rows[0]
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)
            line=make_cmd_run(p,p,row,p,p/'run.tcl',p/'output.log',p/'console.log',profile_only=False)
            self.assertNotIn('PILOT_PROFILE_ONLY',line)
            self.assertIn(f'-testplusarg "FI_OFFSET={row["injection_offset"]}"',line)
            self.assertIn('FI_BIT=',line)
            self.assertIn('FI_OFFSET=',line)
            self.assertIn('set "RC=%ERRORLEVEL%"',line)
            control=make_cmd_run(p,p,row,p,p/'run.tcl',p/'output.log',p/'console.log')
            self.assertIn('-testplusarg "PILOT_PROFILE_ONLY=1"',control)

    def test_realistic_log_injection_identity_and_xor(self):
        row=self.rows[0]
        with tempfile.TemporaryDirectory() as folder:
            f=Path(folder)/'realistic.log'
            f.write_text(fake_fi_log(row))
            r=inspect_applied_and_result(f,row)
            self.assertEqual(r['injection_verified'],'1')
            self.assertEqual(r['temporal_window_verified'],'1')
            self.assertEqual(r['value_aware_consumer_proof'],'NOT_PROVED')
            self.assertEqual(r['final_output_or_protocol_effect'],'0')
            self.assertEqual(set(REPORT_FIELDS),set(r))

    def test_vivado_echo_of_tcl_catch_is_not_runtime_error(self):
        row=self.rows[0]
        with tempfile.TemporaryDirectory() as folder:
            f=Path(folder)/'verbose.log'
            f.write_text('#     catch {puts stderr "CNN_PEP_PILOT400_TCL_ERROR: $err"}\n'
                         +fake_fi_log(row))
            r=inspect_applied_and_result(f,row)
            self.assertEqual(r['injection_verified'],'1')

    def test_wrong_logits_not_wrong_prediction(self):
        row=self.rows[0]
        with tempfile.TemporaryDirectory() as folder:
            f=Path(folder)/'log.txt'
            f.write_text(fake_fi_log(row, outcome='WRONG_LOGITS_SAME_LABEL',logits='1'))
            r=inspect_applied_and_result(f,row)
            self.assertEqual(r['final_output_or_protocol_effect'],'1')
            self.assertEqual(r['value_aware_consumer_proof'],'NOT_PROVED')
            self.assertEqual(r['outcome'],'WRONG_LOGITS_SAME_LABEL')

    def test_pool1_delta_not_consumer_proof(self):
        row=self.rows[0]
        with tempfile.TemporaryDirectory() as folder:
            f=Path(folder)/'log.txt'
            f.write_text(fake_fi_log(row,pool1='1'))
            r=inspect_applied_and_result(f,row)
            self.assertEqual(r['checkpoint_numeric_delta'],'1')
            self.assertEqual(r['value_aware_consumer_proof'],'NOT_PROVED')

    def test_mismatched_injection_is_rejected(self):
        row=self.rows[0]
        with tempfile.TemporaryDirectory() as folder:
            f=Path(folder)/'log.txt'
            for changes in ({'new':'0'}, {'extra':'FI_APPLIED scenario=EXTRA target=CTL_0001 class=CTL bit=0 old=0 new=1 requested=3\n'},
                            {'extra':'ERROR: injected corrupt'},
                            {'extra':'FI_RESULT_V2,duplicate\n'}):
                f.write_text(fake_fi_log(row,**changes))
                with self.assertRaises(ValueError):
                    inspect_applied_and_result(f,row)

    def test_result_identity_mismatch_rejected(self):
        row=self.rows[0]
        with tempfile.TemporaryDirectory() as folder:
            f=Path(folder)/'log.txt'
            f.write_text(fake_fi_log(row).replace(
                f'FI_RESULT_V2,{row["scenario_id"]}',
                'FI_RESULT_V2,FI_P400_FAKE'))
            with self.assertRaises(ValueError):
                inspect_applied_and_result(f,row)

    def test_summary_does_not_auto_promote(self):
        summaries=summarize_targets(self.targets,[],1)
        self.assertEqual(len(summaries),14)
        self.assertTrue(all(r['status']=='INCOMPLETE' for r in summaries))
        self.assertTrue(all(r['value_consumer_proofs']=='0' for r in summaries))
        note=evidence_md(self.targets,self.rows,summaries,False,{})
        self.assertIn('Do not change `evidence_status=CANDIDATE`',note)

    def test_applied_regex_full_coverage(self):
        row=self.rows[0]
        self.assertEqual(len(APPLIED_RE.findall(fake_fi_log(row))),1)


if __name__=='__main__':
    unittest.main()
