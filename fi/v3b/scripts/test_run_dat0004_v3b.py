#!/usr/bin/env python3
"""Fail-closed DAT_0004 Conv2 raw-accumulator -> quant+ReLU consumer tests."""
from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(HERE.parents[1]/'scripts'))
from run_dat0004_v3b import (
    TARGET_ID, SELECTED_IDS, EXPECTED_OFFSETS, V3B_SNAPSHOT, TB_REL,
    COLUMNS, validate_frozen_inputs, selected_dat0004, parse_consumer,
    PROBE_TB_SHA256,
)
from pilot400_common import load_plan, verify_manifest, sha256
from pilot400_direct_xsim import classify_elaboration
from run_mem1_v3b import override_prj

ROOT=HERE.parents[2]
ROW={'scenario_id':'FI_P400_0321','sample_id':'IMG0000','target_id':'DAT_0004',
     'target_class':'DAT','bit_index':'0','injection_offset':'1200'}


def fake_log(row=ROW,original=0x12345678,changed=None,produced=None,
             injected=None,accepted=None,captured=None,relu=0x4A,
             concurrent=1,overwrite=0):
    if changed is None: changed=original^1
    if injected is None: injected=int(row['injection_offset'])
    if produced is None: produced=injected-1
    if accepted is None: accepted=injected
    if captured is None: captured=injected
    sid=row['scenario_id'];target='DAT_0004'
    result=[sid,'IMG0000',target,'DAT',0,injected,injected,injected,
            1,1,1345,784,784,784,169,25,1,1,1,1,0,0,0,0,'NONE','FINAL_OUTPUT_MATCH']
    data=[
      'FI_DISABLED_CONTROL_PASS latency=1342 pool1=676 pool2=200 logits=10 prediction=0',
      f'FI_APPLIED scenario={sid} target={target} class=DAT bit=0 old={original&1} new={changed&1} requested={injected}',
      f'FI_V3B_DAT0004_PRODUCE scenario={sid} target={target} cycle={produced} lane0={original:08x}',
      f'FI_V3B_DAT0004_INJECT scenario={sid} target={target} cycle={injected} bit=0 before={original:08x} after={changed:08x}',
      f'FI_V3B_DAT0004_QUANT_ACCEPT scenario={sid} target={target} cycle={accepted} raw={changed:08x} relu={relu:02x}',
      f'FI_V3B_DAT0004_QUANT_CAPTURE scenario={sid} target={target} cycle={captured} valid=1 relu={relu:02x} concurrent_producer={concurrent}',
      f'FI_V3B_DAT0004_SUMMARY scenario={sid} target={target} inject={injected} consume={accepted} capture={captured} overwritten_before={overwrite} concurrent_producer={concurrent} before={original:08x} after={changed:08x} relu={relu:02x}',
      'FI_RESULT_V2,'+','.join(map(str,result)),
      'CNN_PEP FI CAMPAIGN V2: PASS (mechanism/log integrity; outcome may be harmful)',
    ]
    return '\n'.join(data)+'\n'


class DAT0004Tests(unittest.TestCase):
    def verify(self,txt,good=True,row=ROW,reference=None):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'sim.log';p.write_text(txt,encoding='utf-8')
            ref=None
            if reference is not None:
                ref=Path(d)/'reference.log';ref.write_text(reference,encoding='utf-8')
            if good:
                r=parse_consumer(p,row,ref)
                self.assertEqual(r['consumer_proof'],
                    'VERIFIED_CONV2_RAW_ACC_TO_QUANT_INPUT_AND_REGISTERED_CAPTURE')
                self.assertEqual(r['outcome'],'FINAL_OUTPUT_MATCH')
                self.assertEqual(set(r),set(COLUMNS))
                self.assertEqual(r['original_lane0'],'12345678')
                self.assertEqual(r['corrupted_lane0'],'12345679')
            else:
                with self.assertRaises(ValueError):parse_consumer(p,row,ref)

    def test_frozen_files_and_probe_checksum(self):
        validate_frozen_inputs(ROOT)
        self.assertEqual(sha256(ROOT/TB_REL),PROBE_TB_SHA256)
    def test_frozen_case_pair(self):
        plan=load_plan(ROOT/'fi/config/pilot400_targets.csv',ROOT/'fi/config/fi_targets_candidates_v2.csv')
        rows=selected_dat0004(verify_manifest(ROOT/'fi/scenarios/pilot400.csv',plan))
        self.assertEqual([(r['scenario_id'],r['bit_index'],r['injection_offset']) for r in rows],
                         [('FI_P400_0321','0','1200'),('FI_P400_0322','0','1220')])
    def test_source_replacement_one_only(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'tb_cnn_mnist_fi_pilot400.v';p.write_text('module fake; endmodule')
            prj='verilog xil_defaultlib "C:/RTL/conv2_calc.v"\nverilog xil_defaultlib "C:/TB/tb_cnn_mnist_fi_pilot400.v"\n'
            dst=override_prj(prj,p)
            self.assertIn(p.resolve().as_posix(),dst)
            self.assertIn('C:/RTL/conv2_calc.v',dst)
            self.assertEqual(dst.count('tb_cnn_mnist_fi_pilot400.v'),1)
            with self.assertRaises(ValueError):override_prj(prj+prj,p)
    def test_snapshot_specificity(self):
        with tempfile.TemporaryDirectory() as d:
            snap=Path(d)/'xsim.dir'/V3B_SNAPSHOT;snap.mkdir(parents=True)
            (snap/'xsimk.dll').write_bytes(b'kernel')
            t='Running: xelab.exe --debug typical\nStarting static elaboration\nCompleted static elaboration\nBuilt simulation snapshot '+V3B_SNAPSHOT+'\n'
            self.assertEqual(classify_elaboration(t,snap,None,expected_snapshot=V3B_SNAPSHOT),'FULL_BUILD')
            with self.assertRaises(ValueError):
                classify_elaboration(t.replace(V3B_SNAPSHOT,'some_other_behav'),snap,None,expected_snapshot=V3B_SNAPSHOT)
    def test_case_0321(self):self.verify(fake_log())
    def test_case_0322(self):
        row=dict(ROW,scenario_id='FI_P400_0322',injection_offset='1220')
        self.verify(fake_log(row),row=row)
    def test_matching_reference(self):
        t=fake_log();self.verify(t,reference=t)
    def test_different_outcome_than_v3a(self):
        t=fake_log();self.verify(t,False,reference=t.replace('NONE,FINAL_OUTPUT_MATCH','FC,WRONG_LOGITS_SAME_LABEL'))
    def test_no_concurrent_producer(self):
        self.verify(fake_log(concurrent=0))
    def test_missing_single_bit_xor(self):self.verify(fake_log(changed=0x1234567a),False)
    def test_wrong_lane_in_producer(self):
        self.verify(fake_log().replace('cycle=1199 lane0=12345678','cycle=1199 lane0=12345677'),False)
    def test_wrong_producer_cycle(self):self.verify(fake_log(produced=1198),False)
    def test_accept_next_cycle(self):self.verify(fake_log(accepted=1201),False)
    def test_capture_next_cycle(self):self.verify(fake_log(captured=1201),False)
    def test_wrong_raw_at_consumer(self):
        self.verify(fake_log().replace('raw=12345679','raw=12345678'),False)
    def test_wrong_quant_capture_byte(self):
        self.verify(fake_log().replace('valid=1 relu=4a','valid=1 relu=49'),False)
    def test_wrong_quant_valid(self):
        self.verify(fake_log().replace('valid=1 relu=4a','valid=0 relu=4a'),False)
    def test_invalid_consumer_marker(self):
        self.verify(fake_log()+'FI_V3B_DAT0004_QUANT_INPUT_NOT_PROVED\n',False)
    def test_wrong_summary_overwrite(self):self.verify(fake_log(overwrite=1),False)
    def test_wrong_summary_concurrent(self):
        self.verify(fake_log().replace('overwritten_before=0 concurrent_producer=1','overwritten_before=0 concurrent_producer=0'),False)
    def test_wrong_summary_byte(self):
        self.verify(fake_log().replace('before=12345678 after=12345679 relu=4a',
                                       'before=12345679 after=12345679 relu=4a'),False)
    def test_missing_capture(self):
        self.verify('\n'.join(x for x in fake_log().splitlines()
                              if not x.startswith('FI_V3B_DAT0004_QUANT_CAPTURE'))+'\n',False)
    def test_duplicate_consumer_event(self):
        t=fake_log();ln=next(x for x in t.splitlines() if x.startswith('FI_V3B_DAT0004_QUANT_ACCEPT'))
        self.verify(t+ln+'\n',False)
    def test_wrong_event_order(self):
        lines=fake_log().splitlines();lines[3],lines[4]=lines[4],lines[3]
        self.verify('\n'.join(lines)+'\n',False)
    def test_duplicate_fi_result(self):
        t=fake_log();ln=next(x for x in t.splitlines() if x.startswith('FI_RESULT_V2,'))
        self.verify(t+ln+'\n',False)
    def test_wrong_fi_applied_request(self):
        self.verify(fake_log().replace('requested=1200','requested=1201'),False)
    def test_invalid_case_offset(self):self.verify(fake_log(),False,row=dict(ROW,injection_offset='1199'))
    def test_other_target(self):self.verify(fake_log(),False,row=dict(ROW,target_id='DAT_0002'))
    def test_no_faultfree_baseline(self):
        self.verify(fake_log().replace('FI_DISABLED_CONTROL_PASS','FI_DISABLED_CONTROL_FAIL'),False)
    def test_probe_modified_cannot_pass(self):
        with tempfile.TemporaryDirectory() as d:
            tmp=Path(d)
            for rel in (*[str(k) for k in (
                'fi/config/fi_targets_candidates_v2.csv','fi/config/pilot400_targets.csv',
                'fi/scenarios/pilot400.csv','fi/tb/tb_cnn_mnist_fi_pilot400.v')],str(TB_REL)):
                dest=tmp/rel;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((ROOT/rel).read_bytes())
            tb=tmp/TB_REL;tb.write_text(tb.read_text().replace('QUANT_CAPTURE_NOT_PROVED','QUANT_CAPTURE_NOT_PROV'),encoding='utf-8')
            with self.assertRaises(ValueError):validate_frozen_inputs(tmp)

if __name__=='__main__':unittest.main()
