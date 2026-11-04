#!/usr/bin/env python3
"""V4N offline regression: deterministic pure-functions + optional real pinned sources."""
import csv
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

BASE=Path(__file__).resolve().parent

def load(name):
    spec=importlib.util.spec_from_file_location(name,BASE/(name+'.py'))
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

v=load('validate_checkpoint_witness_v4n')
a=load('audit_checkpoint_witness_v4n')

class PureGolden(unittest.TestCase):
    def setUp(self):self.zeros=[0]*2704
    def test_pool_zeros_676(self):self.assertEqual(v.maxpool2x2(self.zeros),[0]*676)
    def test_recompute_exact_channel_major(self):
        x=list(self.zeros);x[2*676+25*26+25]=7
        pool=v.maxpool2x2(x)
        self.assertEqual(pool[2*169+12*13+12],7)
        self.assertEqual(sum(pool),7)
    def test_bad_shape_denied(self):
        with self.assertRaises(ValueError):v.maxpool2x2([0]*675)
    def test_bad_spatial_position(self):
        with self.assertRaises(ValueError):v.spatial_index(0,13,0,13)
    def test_negative_spatial_position(self):
        with self.assertRaises(ValueError):v.spatial_index(0,-1,0,13)
    def test_pool_effect_case_A(self):
        x=v.injected_pool_witness(self.zeros,1,12)
        self.assertEqual((x['pool1_index'],x['pool1_before'],x['pool1_predicted_after']),(78,0,1))
    def test_pool_effect_case_B(self):
        x=v.injected_pool_witness(self.zeros,5,19)
        self.assertEqual((x['pool1_index'],x['pool1_before'],x['pool1_predicted_after']),(119,0,1))
    def test_pool_effect_other_channel_competing(self):
        x=list(self.zeros);x[12*26]=5
        witness=v.injected_pool_witness(x,1,12)
        self.assertEqual((witness['pool1_before'],witness['pool1_predicted_after']),(5,5))
    def test_nonzero_injection_bit_refused(self):
        with self.assertRaises(ValueError):v.injected_pool_witness(self.zeros,1,12,bit=1)
    def test_outside_coordinates_refused(self):
        with self.assertRaises(ValueError):v.injected_pool_witness(self.zeros,26,12)
    def test_hex_wrong_size_refused(self):
        with self.assertRaises(ValueError):v.hex8_array(b'00\n',2,'p1')
    def test_hex_invalid_octet_refused(self):
        with self.assertRaises(ValueError):v.hex8_array(b'0G\n',1,'p1')
    def test_hex_crlf_allowed(self):
        self.assertEqual(v.hex8_array(b'00\r\nFF\r\n',2,'p1'),[0,255])

class PureMarkers(unittest.TestCase):
    def test_kv_markers_exact(self):
        self.assertEqual(v.kv_marker('FI_APPLIED scenario=X target=T','FI_APPLIED'),{'scenario':'X','target':'T'})
    def test_duplicate_marker_refused(self):
        with self.assertRaises(ValueError):v.kv_marker('FI_APPLIED bit=0 bit=1','FI_APPLIED')
    def test_missing_marker_refused(self):
        with self.assertRaises(ValueError):v.extract_one([],'FI_APPLIED')
    def test_two_markers_refused(self):
        with self.assertRaises(ValueError):v.extract_one(['FI_APPLIED bit=0']*2,'FI_APPLIED')
    def test_invalid_marker_token_refused(self):
        with self.assertRaises(ValueError):v.kv_marker('FI_APPLIED a=1 =2','FI_APPLIED')
    def test_int_overflow_semantics_refused(self):
        with self.assertRaises(ValueError):v.integer({'x':'-1'},'x',minval=0)
    def test_duplicate_json_key_refused(self):
        with self.assertRaises(ValueError):v.json_unique(b'{"ok":1,"ok":2}')
    def test_comparator_diff_detected(self):
        s='if (dut.conv1_out_valid) begin\nA\nif (dut.fc_valid) begin'
        t='if (dut.conv1_out_valid) begin\nB\nif (dut.fc_valid) begin'
        self.assertNotEqual(v.comparator_region(s),v.comparator_region(t))
    def test_comparator_missing(self):
        with self.assertRaises(ValueError):v.comparator_region('missing')

class SyntheticResult(unittest.TestCase):
    def setUp(self):
        self.t={'scenario_id':'FI_P400_0101','sample_id':'IMG0000','target_id':'BUF_0007',
                'target_class':'BUF','bit_index':'0','injection_offset':'791'}
        self.applied='FI_APPLIED scenario=FI_P400_0101 target=BUF_0007 class=BUF bit=0 old=0 new=1 requested=794'
        self.result='FI_RESULT_V2,FI_P400_0101,IMG0000,BUF_0007,BUF,0,791,794,794,1,1,1345,784,784,784,169,25,1,1,1,1,1,0,0,0,POOL1,FINAL_OUTPUT_MATCH'
        self.summary='OUTCOME=FINAL_OUTPUT_MATCH FIRST_BAD_STAGE=POOL1'
    def good(self):return ('\n'.join((self.applied,self.summary,self.result))+'\n').encode()
    def test_marker_parse(self):
        d=v.verify_result(self.good(),self.t,'FINAL_OUTPUT_MATCH',0)
        self.assertEqual((d['pool1_mismatches'],d['pool2_mismatches'],d['l2']),(1,0,True))
    def test_missing_applied(self):
        with self.assertRaises(ValueError):v.verify_result(self.good().replace(self.applied.encode(),b''),self.t,'FINAL_OUTPUT_MATCH',0)
    def test_duplicate_result(self):
        with self.assertRaises(ValueError):v.verify_result(self.good()+self.result.encode()+b'\n',self.t,'FINAL_OUTPUT_MATCH',0)
    def test_wrong_outcome(self):
        with self.assertRaises(ValueError):v.verify_result(self.good(),self.t,'WRONG_LABEL',0)
    def test_wrong_offset(self):
        with self.assertRaises(ValueError):v.verify_result(self.good(),dict(self.t,injection_offset='792'),'FINAL_OUTPUT_MATCH',0)
    def test_incomplete_stage(self):
        with self.assertRaises(ValueError):v.verify_result(self.good().replace(b',169,25,',b',168,25,'),self.t,'FINAL_OUTPUT_MATCH',0)
    def test_wrong_prediction(self):
        with self.assertRaises(ValueError):v.verify_result(self.good(),self.t,'FINAL_OUTPUT_MATCH',1)
    def test_wrong_logit(self):
        with self.assertRaises(ValueError):v.verify_result(self.good().replace(b',1,0,0,0,POOL1',b',1,0,0,1,POOL1'),self.t,'FINAL_OUTPUT_MATCH',0)
    def test_no_event(self):
        with self.assertRaises(ValueError):v.verify_result(self.good().replace(b'794,794,1,1,',b'794,794,0,1,'),self.t,'FINAL_OUTPUT_MATCH',0)

class OutputSafety(unittest.TestCase):
    def test_report_no_release_claim(self):
        s={'witnesses':[]}
        self.assertIn(b'FULL400 BLOCKED',v.report(s))
    def test_canonical_json_lf(self):
        b=v.bytes_json({'b':2,'a':1})
        self.assertEqual(b,b'{\n  "a": 1,\n  "b": 2\n}\n')
    def test_extra_output_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'v4n_test_01';path.mkdir();(path/'garbage.txt').write_text('bad')
            with self.assertRaises(ValueError):a.audit_output(path)
    def test_existing_output_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'v4n_test_01';path.mkdir()
            with self.assertRaises(ValueError):v.write_new(path,{'foo.txt':b'bar'})
    def test_output_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):v.write_new(Path(temp)/'fi_results',{'foo.txt':b'bar'})
    def test_json_auditor_reject_duplicates(self):
        with self.assertRaises(ValueError):a.parse_json(b'{"status":true,"status":false}')
    def test_output_sha_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp)/'v4n_tamper_01';d.mkdir()
            for f in a.EXPECTED_FILES:(d/f).write_bytes(b'x')
            (d/'SHA256SUMS.txt').write_bytes(b'not real')
            with self.assertRaises(ValueError):a.audit_output(d)

class RealSourceIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.handoff=os.environ.get('CNN_PEP_V4N_SOURCE_ZIP')
        cls.repo=Path(__file__).resolve().parents[3]
        if cls.handoff and Path(cls.handoff).is_file():cls.mode={'archive':cls.handoff}
        elif (cls.repo/'fi/results/v4c_readiness_audit_01/v4c_target_evidence_matrix.csv').is_file():cls.mode={'repo':str(cls.repo)}
        else:cls.mode=None
    def test_real_v4c_40_recompute(self):
        if not self.mode:self.skipTest('No complete historical raw source mounted')
        inp=v.Source(**self.mode)
        try:s=v.analyze(inp)
        finally:inp.close()
        self.assertEqual((s['l0_positive'],s['l1_positive'],s['l2_positive']),(9,20,24))
        self.assertEqual(len(s['witnesses']),4)
        self.assertEqual(s['l2_false_complete_count'],16)

if __name__=='__main__':unittest.main()
