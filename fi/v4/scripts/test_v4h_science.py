"""Offline source-and-science guard negative tests. No Vivado/XSim needed."""
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parent

def module(filename, name):
    spec=importlib.util.spec_from_file_location(name,SCRIPTS/filename)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
A=module('analyze_v4c_observability_v4h.py','v4h_analyzer')
B=module('audit_v4h_science.py','v4h_auditor')


def fixture():
    frozen=[]
    targets=[]; matrix=[]
    outs=['NONCOMPLETION','WRONG_LOGITS_SAME_LABEL','FINAL_OUTPUT_MATCH',
          'WRONG_LABEL','PROTOCOL_VIOLATION']
    for t in range(20):
        target=f'{A.CLASSES[t//5]}_{t:04d}'
        for s in range(10):
            for config in range(2):
                sid=f'FI_P400_{len(frozen)+1:04d}'
                frozen.append(dict(scenario_id=sid,sample_id=f'IMG{s:04d}',
                   target_id=target,target_class=A.CLASSES[t//5],
                   bit_index=str(config),injection_offset=str(100+config)))
        a,b=frozen[-20],frozen[-19]
        # in synthetic tuple order, first A and B belong to IMG0000
        a,b=frozen[-20],frozen[-19]
        matrix.append(dict(target_id=target,target_class=A.CLASSES[t//5],
          frozen_evidence_status='CANDIDATE',config_a_scenario=a['scenario_id'],
          config_b_scenario=b['scenario_id'],config_a_bit='0',config_b_bit='1',
          config_a_offset='100',config_b_offset='101',proof_source='V3B_ISOLATED',
          proof_kind='E3_A | E3_B',config_a_outcome=outs[t%5],
          config_b_outcome=outs[t%5],config_a_log_sha256='a'*64,
          config_b_log_sha256='b'*64,
          directed_review='TWO_CONFIG_CONSUMER_DECISION_PROVED',
          automatic_promotion='NO',full400_authorized='NO'))
    return frozen,matrix

class ScienceModel(unittest.TestCase):
    def setUp(self):
        self.frozen,self.matrix=fixture()

    def test_40_directed_records_and_evaluable(self):
        report,details,targets=A.analyze(self.frozen,self.matrix)
        self.assertEqual(len(details),40)
        self.assertEqual(len(targets),20)
        self.assertEqual(report['new_xsim_runs'],0)
        self.assertFalse(report['full400_authorized'])

    def test_l2_not_auto_match_for_final_match(self):
        _,details,_=A.analyze(self.frozen,self.matrix)
        self.assertTrue(all(r['L2']=='INCONCLUSIVE' for r in details
                            if r['outcome']=='FINAL_OUTPUT_MATCH'))

    def test_l2_true_inherited_from_l1(self):
        _,details,_=A.analyze(self.frozen,self.matrix)
        self.assertTrue(all(r['L2']=='TRUE' for r in details if r['L1']=='TRUE'))

    def test_reject_duplicate_target(self):
        self.matrix[1]['target_id']=self.matrix[0]['target_id']
        with self.assertRaises(ValueError):A.analyze(self.frozen,self.matrix)

    def test_reject_modified_bit(self):
        self.matrix[0]['config_a_bit']='77'
        with self.assertRaises(ValueError):A.analyze(self.frozen,self.matrix)

    def test_reject_modified_offset(self):
        self.matrix[0]['config_a_offset']='999'
        with self.assertRaises(ValueError):A.analyze(self.frozen,self.matrix)

    def test_reject_unauthorized_marker(self):
        self.matrix[0]['full400_authorized']='YES'
        with self.assertRaises(ValueError):A.analyze(self.frozen,self.matrix)

    def test_reject_no_e3(self):
        self.matrix[0]['directed_review']='NOT_MONITORED'
        with self.assertRaises(ValueError):A.analyze(self.frozen,self.matrix)

    def test_reject_extra_scenario(self):
        self.matrix[0]['config_a_scenario']='FI_P400_0003'
        with self.assertRaises(ValueError):A.analyze(self.frozen,self.matrix)

    def test_reject_bad_outcome(self):
        self.matrix[0]['config_a_outcome']='MATCHED'
        with self.assertRaises(ValueError):A.analyze(self.frozen,self.matrix)

    def test_reject_missing_sha(self):
        self.matrix[0]['config_a_log_sha256']=''
        with self.assertRaises(ValueError):A.analyze(self.frozen,self.matrix)

    def test_reject_duplicate_json_key(self):
        with self.assertRaises(ValueError):json.loads('{"x":1,"x":2}',object_pairs_hook=A.no_duplicates)

    def test_directed_not_assumed_seu(self):
        result,_,_=A.analyze(self.frozen,self.matrix)
        self.assertTrue(any('SEU' in s for s in result['limitations']))

    def test_no_command_execution_interface(self):
        cp=subprocess.run([sys.executable,str(SCRIPTS/'analyze_v4c_observability_v4h.py'),
                 '--execute'],capture_output=True,text=True)
        self.assertNotEqual(cp.returncode,0)

    def test_output_has_four_deterministic_artifacts(self):
        result,details,targets=A.analyze(self.frozen,self.matrix)
        self.assertTrue(A.json_bytes(result).endswith(b'\n'))
        self.assertEqual(len(list(csv.DictReader(io.StringIO(A.csv_bytes(details).decode())))),40)
        self.assertIn(b'INCONCLUSIVE',A.report_md(result))

class AuditGuards(unittest.TestCase):
    def test_refuse_outside_results_without_read(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'fi/results').mkdir(parents=True)
            with self.assertRaises(ValueError):B.audit(root,root)

    def test_refuse_incomplete_artifacts(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);out=root/'fi/results/v4h_bad';out.mkdir(parents=True)
            with self.assertRaises(ValueError):B.audit(root,out)

if __name__=='__main__':unittest.main()
