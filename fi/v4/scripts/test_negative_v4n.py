#!/usr/bin/env python3
"""V4N real-data negative integration tests. All tampering occurs on temporary copies."""
import csv
import hashlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

SCRIPTS=Path(__file__).resolve().parent

def load(mod):
    spec=importlib.util.spec_from_file_location(mod,SCRIPTS/(mod+'.py'))
    out=importlib.util.module_from_spec(spec);spec.loader.exec_module(out)
    return out

v=load('validate_checkpoint_witness_v4n')
a=load('audit_checkpoint_witness_v4n')

@contextmanager
def modified(file, change):
    path=Path(file)
    saved=path.read_bytes()
    try:
        path.write_bytes(change(saved))
        yield
    finally:
        path.write_bytes(saved)

class RealSourceNegative(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        zipname=os.environ.get('CNN_PEP_V4N_SOURCE_ZIP')
        repo=Path(__file__).resolve().parents[3]
        if zipname and Path(zipname).is_file():args={'archive':zipname}
        elif (repo/'fi/results/v4c_readiness_audit_01/v4c_target_evidence_matrix.csv').is_file():args={'repo':str(repo)}
        else:raise unittest.SkipTest('No complete historical V4c raw evidence mounted')
        cls.tmp=tempfile.TemporaryDirectory(prefix='v4n_negative_fixture_')
        cls.root=Path(cls.tmp.name)/'repo'
        src=v.Source(**args)
        try:
            base=set(v.PIN)|{
                'fi/results/v4c_readiness_audit_01/v4c_manifest.json',
                'fi/results/pilot400_v3b_buf0007_01/v3b_buf0007_manifest.json',
                'fi/results/pilot400_v3b_dat0002_01/v3b_dat0002_manifest.json',
                'fi/v3b/buf0007/tb/tb_cnn_mnist_fi_pilot400.v',
                'fi/v3b/dat0002/tb/tb_cnn_mnist_fi_pilot400.v',
            }
            for folder, ids in (('buf0007',(101,102)),('dat0002',(301,302))):
                for n in ids:base.add(f'fi/results/pilot400_v3b_{folder}_01/logs/FI_P400_{n:04d}.log')
            expectations={}
            matrix=v.read_csv(src.read('fi/results/v4c_readiness_audit_01/v4c_target_evidence_matrix.csv'))
            for row in matrix:
                for suffix in ('a','b'):
                    expectations[row[f'config_{suffix}_log_sha256']]=row[f'config_{suffix}_scenario']
            for rel,raw in src.matching_log_data():
                h=v.digest(raw)
                if h in expectations:
                    sid=expectations[h]
                    if sid not in cls.__dict__.get('_first_seen',set()):
                        cls._first_seen=getattr(cls,'_first_seen',set())|{sid}
                        base.add(rel)
            if len(getattr(cls,'_first_seen',set()))!=40:raise RuntimeError('Failed to collect 40 pinned logs')
            for rel in base:
                path=cls.root/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(src.read(rel))
        finally:src.close()
        cls.source=staticmethod(lambda: v.Source(repo=cls.root))
        c=cls.source()
        try:cls.result=v.analyze(c)
        finally:c.close()
        cls.analysis=v.write_new(Path(cls.tmp.name)/'v4n_negative_baseline_01',v.artifacts(cls.result))
        cls.replay={}
        c=a.Input(cls.root,None)
        try:cls.replay=a.independent_replay(c)
        finally:c.close()
        cls.audit_clean=a.audit_output(cls.analysis)
        a.verify(cls.audit_clean,cls.replay)
    @classmethod
    def tearDownClass(cls):
        if hasattr(cls,'tmp'):cls.tmp.cleanup()
    def assert_source_denied(self,rel,callback):
        path=self.root/rel
        with modified(path,callback):
            source=self.source()
            try:
                with self.assertRaises((ValueError,KeyError,FileNotFoundError)):
                    v.analyze(source)
            finally:source.close()
    def rewrite_sums(self, folder):
        files=sorted(p for p in folder.iterdir() if p.name!='SHA256SUMS.txt')
        content=''.join(v.digest(p.read_bytes())+'  '+p.name+'\n' for p in files)
        (folder/'SHA256SUMS.txt').write_bytes(content.encode('ascii'))
    def audit_mutated_output(self, filename, callback):
        with tempfile.TemporaryDirectory(prefix='v4n_output_mut_') as temp:
            d=Path(temp)/'v4n_negative_output_01'
            shutil.copytree(self.analysis,d)
            path=d/filename
            path.write_bytes(callback(path.read_bytes()))
            self.rewrite_sums(d)
            with self.assertRaises((ValueError,KeyError,TypeError)):
                audit=a.audit_output(d)
                a.verify(audit,self.replay)
    def test_golden_relu_drift_denied(self):
        self.assert_source_denied('data/golden_relu1.hex',lambda b:b.replace(b'00',b'01',1))
    def test_golden_pool1_drift_denied(self):
        self.assert_source_denied('fi/pilot400/samples/IMG0000/golden_pool1.hex',lambda b:b.replace(b'00',b'01',1))
    def test_v4c_matrix_drift_denied(self):
        self.assert_source_denied('fi/results/v4c_readiness_audit_01/v4c_target_evidence_matrix.csv',lambda b:b.replace(b'FI_P400_0101',b'FI_P400_0103',1))
    def test_v3b_trace_log_drift_denied(self):
        self.assert_source_denied('fi/results/pilot400_v3b_buf0007_01/logs/FI_P400_0101.log',lambda b:b+b'altered\n')
    def test_original_tb_drift_denied(self):
        self.assert_source_denied('fi/tb/tb_cnn_mnist_fi_pilot400.v',lambda b:b+b'// fake\n')
    def test_isolated_probe_tb_drift_denied(self):
        self.assert_source_denied('fi/v3b/dat0002/tb/tb_cnn_mnist_fi_pilot400.v',lambda b:b+b'// fake\n')
    def test_final_match_log_stage_mutation_denied(self):
        rows=csv.DictReader(io.StringIO((self.analysis/'v4n_sixteen_l2_false_complete.csv').read_text()))
        sid=next(rows)['scenario_id']
        matches=list(self.root.glob('fi/results/**/logs/'+sid+'.log'))
        self.assertTrue(matches)
        path=matches[0]
        rel=path.relative_to(self.root)
        self.assert_source_denied(str(rel),lambda b:b.replace(b',169,25,',b',168,25,',1))
    def test_release_flag_forgery_even_rehashed_denied(self):
        def mutate(b):
            val=json.loads(b);val['full400_authorized']=True
            return v.bytes_json(val)
        self.audit_mutated_output('v4n_golden_trace_summary.json',mutate)
    def test_negative_csv_counter_forgery_even_rehashed_denied(self):
        self.audit_mutated_output('v4n_sixteen_l2_false_complete.csv',lambda b:b.replace(b',169,25,',b',168,25,',1))
    def test_witness_csv_index_forgery_even_rehashed_denied(self):
        self.audit_mutated_output('v4n_four_l2_witnesses.csv',lambda b:b.replace(b'337,0,1',b'336,0,1',1))
    def test_duplicate_json_key_even_rehashed_denied(self):
        def mutate(b):return b.replace(b'  "schema":',b'  "schema":"FAKE",\n  "schema":',1)
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp)/'v4n_negative_dup_01';shutil.copytree(self.analysis,d)
            (d/'v4n_golden_trace_summary.json').write_bytes(mutate((d/'v4n_golden_trace_summary.json').read_bytes()))
            self.rewrite_sums(d)
            with self.assertRaises((ValueError,KeyError,TypeError)):a.audit_output(d)
    def test_crlf_json_even_rehashed_denied(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp)/'v4n_negative_crlf_01';shutil.copytree(self.analysis,d)
            p=d/'v4n_golden_trace_summary.json';p.write_bytes(p.read_bytes().replace(b'\n',b'\r\n'))
            self.rewrite_sums(d)
            with self.assertRaises((ValueError,KeyError,TypeError)):a.audit_output(d)

if __name__=='__main__':unittest.main()
