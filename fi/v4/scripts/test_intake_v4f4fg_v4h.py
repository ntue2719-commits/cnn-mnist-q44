import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('v4h_intake',HERE/'intake_v4f4fg_v4h.py')
I=importlib.util.module_from_spec(spec);spec.loader.exec_module(I)

class IntakeGuards(unittest.TestCase):
    def test_forbidden_release(self):
        with self.assertRaises(ValueError):I.scan_no_claim({'full400_authorized':True},'fixture')
    def test_infrastructure_cannot_be_xsim(self):
        with self.assertRaises(ValueError):I.scan_no_claim({'new_xsim_runs':1},'fixture')
    def test_synthetic_stays_no_xsim(self):
        I.scan_no_claim({'new_xsim_runs':0,'xsim_executed':False,'records':[
            {'injection_executed':False}]},'fixture')
    def test_duplicate_json_key_rejected(self):
        with self.assertRaises(ValueError):json.loads('{"a":false,"a":true}',
                                                    object_pairs_hook=I.duplicate_json_keys)
    def test_refuse_missing_repo(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):I.collect(Path(d))
    def test_refuse_unbounded_file(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'big.txt'
            p.write_bytes(b'hi')
            before=I.MAX_SINGLE_BYTES
            try:
                I.MAX_SINGLE_BYTES=1
                with self.assertRaises(ValueError):I.hash_file(p)
            finally:
                I.MAX_SINGLE_BYTES=before
    def test_mock_readonly_inventory_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d)
            (repo/'fi/results').mkdir(parents=True)
            with mock.patch.object(I,'FROZEN',{}):
                for rel in I.SCRIPTS:
                    file=repo/rel;file.parent.mkdir(parents=True,exist_ok=True)
                    file.write_text('"not execution code"',encoding='utf-8')
                for rel in I.DIRS:
                    folder=repo/rel;folder.mkdir(parents=True)
                    (folder/'a.json').write_text('{"full400_authorized":false,"new_xsim_runs":0}',encoding='utf-8')
                out=Path('fi/results/v4h_mock_intake')
                report=I.run(repo,out,'inventory')
                self.assertEqual(report['total_source_files'],8)
                self.assertFalse(report['invocation_contracts_semantically_verified'])
                self.assertEqual(report['new_xsim_runs'],0)
                with self.assertRaises(ValueError):I.run(repo,out,'inventory')

    def test_mock_evidence_refuses_forged_approval(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d)
            (repo/'fi/results').mkdir(parents=True)
            with mock.patch.object(I,'FROZEN',{}):
                for rel in I.SCRIPTS:
                    p=repo/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# mock')
                for rel in I.DIRS:
                    folder=repo/rel;folder.mkdir(parents=True)
                    (folder/'a.json').write_text('{"full400_authorized":false}')
                (repo/I.DIRS[0]/'a.json').write_text('{"full400_authorized":true}')
                with self.assertRaises(ValueError):I.collect(repo)

    def test_schema_not_g6_pass(self):
        self.assertNotIn('G6_QUALIFIED',I.SCHEMA)

if __name__=='__main__':unittest.main()
