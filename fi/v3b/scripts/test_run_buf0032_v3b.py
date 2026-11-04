#!/usr/bin/env python3
"""Fail-closed BUF_0032 ch3/P8 pre-NBA consumer-proof parser and integrity tests."""
from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(HERE.parents[1]/'scripts'))
from run_buf0032_v3b import (
    TARGET_ID, SELECTED_IDS, EXPECTED_OFFSETS, V3B_SNAPSHOT, TB_REL, COLUMNS,
    validate_frozen_inputs, selected_buf0032, parse_consumer)
from pilot400_common import load_plan, verify_manifest, sha256
from pilot400_direct_xsim import classify_elaboration
from run_mem1_v3b import override_prj

ROOT=HERE.parents[2]
ROW={'scenario_id':'FI_P400_0181','sample_id':'IMG0000','target_id':'BUF_0032',
     'target_class':'BUF','bit_index':'0','injection_offset':'1135'}


def fake_log(row=ROW,original=0x54,changed=None,write=1131,inject=1135,read=1258):
    if changed is None:changed=original^1
    sid=row['scenario_id'];offset=int(row['injection_offset']);consume=read+1
    data=[
      'FI_DISABLED_CONTROL_PASS latency=1342 pool1=676 pool2=200 logits=10 prediction=0',
      f'FI_APPLIED scenario={sid} target=BUF_0032 class=BUF bit=0 old={original&1} new={changed&1} requested={inject}',
      f'FI_V3B_BUF0032_WRITE scenario={sid} target=BUF_0032 cycle={write} byte={original:02x}',
      f'FI_V3B_BUF0032_INJECT scenario={sid} target=BUF_0032 cycle={inject} bit=0 before={original:02x} after={changed:02x}',
      f'FI_V3B_BUF0032_READ scenario={sid} target=BUF_0032 cycle={read} y=10 x=10 byte={changed:02x}',
      f'FI_V3B_BUF0032_WINDOW scenario={sid} target=BUF_0032 cycle={read} slot=35 byte={changed:02x}',
      f'FI_V3B_BUF0032_MAC_CONSUME scenario={sid} target=BUF_0032 cycle={consume} slot=35 byte={changed:02x}',
      f'FI_V3B_BUF0032_SUMMARY scenario={sid} target=BUF_0032 write={write} inject={inject} read={read} window={read} consume={consume} overwrites=0 before={original:02x} after={changed:02x}',
    ]
    result=[sid,'IMG0000','BUF_0032','BUF',0,offset,inject,inject,
            1,1,1345,784,784,784,169,25,1,1,1,1,0,0,0,0,'NONE','FINAL_OUTPUT_MATCH']
    data.append('FI_RESULT_V2,'+','.join(map(str,result)))
    data.append('CNN_PEP FI CAMPAIGN V2: PASS (mechanism/log integrity; outcome may be harmful)')
    return '\n'.join(data)+'\n'


class Buf0032ProofTests(unittest.TestCase):
    def verify(self,text,good=True,row=ROW,reference=None):
        with tempfile.TemporaryDirectory() as d:
            log=Path(d)/'scenario.log';log.write_text(text,encoding='utf-8')
            ref=None
            if reference is not None:
                ref=Path(d)/'reference.log';ref.write_text(reference,encoding='utf-8')
            if good:
                parsed=parse_consumer(log,row,ref)
                self.assertEqual(parsed['consumer_proof'],'VERIFIED_CONV2_BUF675_TO_MAC_CH3_P8')
                self.assertEqual(parsed['outcome'],'FINAL_OUTPUT_MATCH')
                self.assertEqual(parsed['overwrites'],'0')
                self.assertEqual(set(parsed),set(COLUMNS))
            else:
                with self.assertRaises(ValueError):parse_consumer(log,row,ref)
    def test_frozen_inputs_and_monitor_only(self):
        validate_frozen_inputs(ROOT)
        self.assertNotEqual(sha256(ROOT/TB_REL),sha256(ROOT/'fi/tb/tb_cnn_mnist_fi_pilot400.v'))
    def test_frozen_pair_identity(self):
        plan=load_plan(ROOT/'fi/config/pilot400_targets.csv',ROOT/'fi/config/fi_targets_candidates_v2.csv')
        rows=selected_buf0032(verify_manifest(ROOT/'fi/scenarios/pilot400.csv',plan))
        self.assertEqual([(x['scenario_id'],x['bit_index'],x['injection_offset']) for x in rows],
            [('FI_P400_0181','0','1135'),('FI_P400_0182','0','1136')])
    def test_only_tb_path_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            tb=Path(d)/'tb_cnn_mnist_fi_pilot400.v';tb.write_text('module fake;endmodule')
            prj='verilog xil_defaultlib "C:/RTL/conv2_buf.v"\nverilog xil_defaultlib "C:/Src/tb_cnn_mnist_fi_pilot400.v"\n'
            new=override_prj(prj,tb)
            self.assertIn(tb.resolve().as_posix(),new)
            self.assertIn('C:/RTL/conv2_buf.v',new)
            self.assertEqual(new.count('tb_cnn_mnist_fi_pilot400.v'),1)
            with self.assertRaises(ValueError):override_prj(prj+prj,tb)
    def test_snapshot_name(self):
        with tempfile.TemporaryDirectory() as d:
            snap=Path(d)/'xsim.dir'/V3B_SNAPSHOT;snap.mkdir(parents=True)
            (snap/'xsimk.dll').write_bytes(b'kernel')
            s='Running: xelab.exe\nStarting static elaboration\nCompleted static elaboration\nBuilt simulation snapshot '+V3B_SNAPSHOT+'\n'
            self.assertEqual(classify_elaboration(s,snap,None,expected_snapshot=V3B_SNAPSHOT),'FULL_BUILD')
            with self.assertRaises(ValueError):
                classify_elaboration(s.replace(V3B_SNAPSHOT,'old_snapshot'),snap,None,expected_snapshot=V3B_SNAPSHOT)
    def test_good_case_0181(self):self.verify(fake_log())
    def test_good_case_0182(self):
        r=dict(ROW,scenario_id='FI_P400_0182',injection_offset='1136')
        self.verify(fake_log(r,inject=1136),row=r)
    def test_good_reference_matches(self):
        t=fake_log();self.verify(t,reference=t)
    def test_reference_outcome_mismatch(self):
        t=fake_log();self.verify(t,False,reference=t.replace('NONE,FINAL_OUTPUT_MATCH','FC,WRONG_LOGITS_SAME_LABEL'))
    def test_bad_xor(self):self.verify(fake_log(changed=0x56),False)
    def test_wrong_write_byte(self):
        self.verify(fake_log().replace('cycle=1131 byte=54','cycle=1131 byte=53'),False)
    def test_wrong_read_coordinates(self):
        self.verify(fake_log().replace('cycle=1258 y=10 x=10','cycle=1258 y=10 x=9'),False)
    def test_wrong_read_byte(self):
        self.verify(fake_log().replace('y=10 x=10 byte=55','y=10 x=10 byte=54'),False)
    def test_wrong_window_slot(self):
        self.verify(fake_log().replace('cycle=1258 slot=35','cycle=1258 slot=34'),False)
    def test_wrong_mac_slot(self):
        self.verify(fake_log().replace('cycle=1259 slot=35','cycle=1259 slot=27'),False)
    def test_wrong_mac_value(self):
        self.verify(fake_log().replace('cycle=1259 slot=35 byte=55','cycle=1259 slot=35 byte=54'),False)
    def test_nonconsecutive_consume(self):
        self.verify(fake_log().replace('cycle=1259 slot=35','cycle=1260 slot=35'),False)
    def test_read_window_different_edge(self):
        self.verify(fake_log().replace('WINDOW scenario=FI_P400_0181 target=BUF_0032 cycle=1258',
                                      'WINDOW scenario=FI_P400_0181 target=BUF_0032 cycle=1257'),False)
    def test_injected_after_read(self):self.verify(fake_log(inject=1260),False)
    def test_missing_read(self):
        self.verify('\n'.join(x for x in fake_log().splitlines() if not x.startswith('FI_V3B_BUF0032_READ '))+'\n',False)
    def test_duplicate_mac(self):
        s=fake_log();line=next(x for x in s.splitlines() if x.startswith('FI_V3B_BUF0032_MAC_CONSUME '))
        self.verify(s+line+'\n',False)
    def test_out_of_order(self):
        lines=fake_log().splitlines();lines[4],lines[5]=lines[5],lines[4]
        self.verify('\n'.join(lines)+'\n',False)
    def test_overwrites(self):self.verify(fake_log().replace('overwrites=0','overwrites=1'),False)
    def test_wrong_summary_timeline(self):
        self.verify(fake_log().replace('window=1258 consume=1259','window=1257 consume=1259'),False)
    def test_wrong_summary_bit(self):
        self.verify(fake_log().replace('overwrites=0 before=54','overwrites=0 before=55'),False)
    def test_missing_summary(self):
        self.verify('\n'.join(x for x in fake_log().splitlines() if not x.startswith('FI_V3B_BUF0032_SUMMARY '))+'\n',False)
    def test_infra_failure_marker(self):self.verify(fake_log()+'FI_V3B_BUF0032_READ_VALUE_NOT_PROVED\n',False)
    def test_duplicate_fi_result(self):
        s=fake_log();line=next(x for x in s.splitlines() if x.startswith('FI_RESULT_V2,'));self.verify(s+line+'\n',False)
    def test_wrong_injection_requested(self):self.verify(fake_log().replace('requested=1135','requested=1136'),False)
    def test_wrong_identity(self):self.verify(fake_log(),False,row=dict(ROW,target_id='BUF_0031'))
    def test_invalid_manifest_offset(self):self.verify(fake_log(),False,row=dict(ROW,injection_offset='1134'))
    def test_wrong_pair_offset(self):self.verify(fake_log(),False,row=dict(ROW,injection_offset='1136'))
    def test_control_missing(self):self.verify(fake_log().replace('FI_DISABLED_CONTROL_PASS','CONTROL_NOT_PRESENT'),False)
    def test_source_monitor_tampered(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for rel in ('fi/config/fi_targets_candidates_v2.csv','fi/config/pilot400_targets.csv',
                        'fi/scenarios/pilot400.csv','fi/tb/tb_cnn_mnist_fi_pilot400.v',str(TB_REL)):
                dest=root/rel;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((ROOT/rel).read_bytes())
            p=root/TB_REL;p.write_text(p.read_text(encoding='utf-8').replace('MAC_INPUT_NOT_PROVED','MAC_INPUT_NOT_PROVE'),encoding='utf-8')
            with self.assertRaises(ValueError):validate_frozen_inputs(root)

if __name__=='__main__':unittest.main()
