#!/usr/bin/env python3
"""Offline positive/negative integrity tests for BUF_0017 dual-branch pre-NBA consumer proof."""
from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(HERE.parents[1]/'scripts'))
from run_buf0017_v3b import (
    TARGET_ID,SELECTED_IDS,EXPECTED_OFFSETS,V3B_SNAPSHOT,TB_REL,COLUMNS,
    validate_frozen_inputs,selected_buf0017,parse_consumer)
from pilot400_common import load_plan,verify_manifest,sha256
from pilot400_direct_xsim import classify_elaboration
from run_mem1_v3b import override_prj

ROOT=HERE.parents[2]
ROW={'scenario_id':'FI_P400_0121','sample_id':'IMG0000','target_id':'BUF_0017',
     'target_class':'BUF','bit_index':'0','injection_offset':'34'}


def fake_log(row=ROW,orig=0x2e,after=None,write=32,inject=37,read=61):
    if after is None:after=orig^1
    sid=row['scenario_id'];offset=int(row['injection_offset'])
    cycles=[write,inject,read,read+1,read+2,read+3,
            read+28,read+29,read+30,read+31]
    names=('WRITE','INJECT','READ','MID_SHIFT','MID_WINDOW','MID_MAC',
           'TOP_READ','TOP_SHIFT','TOP_WINDOW','TOP_MAC')
    l=['FI_DISABLED_CONTROL_PASS latency=1342 pool1=676 pool2=200 logits=10 prediction=0',
       f'FI_APPLIED scenario={sid} target=BUF_0017 class=BUF bit=0 old={orig&1} new={after&1} requested={inject}']
    for i,(name,cyc) in enumerate(zip(names,cycles)):
        ext=(f' bit=0 before={orig:02x} after={after:02x}' if name=='INJECT' else
             f' slot={3 if name.startswith("MID") else 0} byte={after:02x}' if name.endswith(('WINDOW','MAC')) else
             f' byte={orig if name=="WRITE" else after:02x}')
        l.append(f'FI_V3B_BUF0017_{name} scenario={sid} target=BUF_0017 cycle={cyc}{ext}')
    l.append(f'FI_V3B_BUF0017_SUMMARY scenario={sid} target=BUF_0017 '+
        ' '.join(f'{key}={c}' for key,c in zip(('write','inject','read','midshift',
            'midwindow','midmac','topread','topshift','topwindow','topmac'),cycles))+
        f' source_overwrites=1 secondary_overwrites=1 before={orig:02x} after={after:02x}')
    result=[sid,'IMG0000','BUF_0017','BUF',0,offset,inject,inject,
            1,1,1345,784,784,784,169,25,1,1,1,1,0,0,0,0,'NONE','FINAL_OUTPUT_MATCH']
    l.append('FI_RESULT_V2,'+','.join(map(str,result)))
    l.append('CNN_PEP FI CAMPAIGN V2: PASS (mechanism/log integrity; outcome may be harmful)')
    return '\n'.join(l)+'\n'

class Buf0017Tests(unittest.TestCase):
    def verify(self,text,good=True,row=ROW,ref=None):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'log.txt';p.write_text(text,encoding='utf-8')
            refpath=None
            if ref is not None:
                refpath=Path(tmp)/'v3a.log';refpath.write_text(ref,encoding='utf-8')
            if good:
                obj=parse_consumer(p,row,refpath)
                self.assertEqual(obj['consumer_proof'],'VERIFIED_LINEBUF1_TO_CONV1_MAC_P3_AND_P0')
                self.assertEqual(obj['outcome'],'FINAL_OUTPUT_MATCH')
                self.assertEqual(obj['source_overwrites'],'1')
                self.assertEqual(set(obj),set(COLUMNS))
            else:
                with self.assertRaises(ValueError):parse_consumer(p,row,refpath)
    def test_original_hashes_unchanged(self):
        validate_frozen_inputs(ROOT)
        self.assertNotEqual(sha256(ROOT/TB_REL),sha256(ROOT/'fi/tb/tb_cnn_mnist_fi_pilot400.v'))
    def test_exact_frozen_pair(self):
        plan=load_plan(ROOT/'fi/config/pilot400_targets.csv',ROOT/'fi/config/fi_targets_candidates_v2.csv')
        manifest=verify_manifest(ROOT/'fi/scenarios/pilot400.csv',plan)
        rows=selected_buf0017(manifest)
        self.assertEqual([(r['scenario_id'],r['bit_index'],r['injection_offset']) for r in rows],
                         [('FI_P400_0121','0','34'),('FI_P400_0122','0','35')])
    def test_original_source_list_only_tb_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            tb=Path(tmp)/'tb_cnn_mnist_fi_pilot400.v';tb.write_text('module fake;endmodule')
            prj='verilog xil_defaultlib "C:/Sim/tb_cnn_mnist_fi_pilot400.v"\nverilog xil_defaultlib "C:/Sim/conv1_buf.v"\n'
            patched=override_prj(prj,tb)
            self.assertIn(tb.resolve().as_posix(),patched)
            self.assertIn('C:/Sim/conv1_buf.v',patched)
            self.assertEqual(patched.count('tb_cnn_mnist_fi_pilot400.v'),1)
            with self.assertRaises(ValueError):override_prj(prj+prj,tb)
    def test_snapshot_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'xsim.dir'/V3B_SNAPSHOT;path.mkdir(parents=True)
            (path/'xsimk.dll').write_bytes(b'kernel')
            s=f'Running: xelab.exe\nStarting static elaboration\nCompleted static elaboration\nBuilt simulation snapshot {V3B_SNAPSHOT}\n'
            self.assertEqual(classify_elaboration(s,path,None,expected_snapshot=V3B_SNAPSHOT),'FULL_BUILD')
            with self.assertRaises(ValueError):
                classify_elaboration(s.replace(V3B_SNAPSHOT,'another_behav'),path,None,expected_snapshot=V3B_SNAPSHOT)
    def test_good_0121(self):self.verify(fake_log())
    def test_good_0122(self):
        row=dict(ROW,scenario_id='FI_P400_0122',injection_offset='35')
        self.verify(fake_log(row=row,inject=38),row=row)
    def test_reference_agrees(self):
        sample=fake_log();self.verify(sample,ref=sample)
    def test_reference_outcome_differs(self):
        sample=fake_log();self.verify(sample,False,ref=sample.replace('NONE,FINAL_OUTPUT_MATCH','FC,WRONG_LOGITS_SAME_LABEL'))
    def test_wrong_byte_xor(self):self.verify(fake_log(after=0x2c),False)
    def test_wrong_write_before(self):
        self.verify(fake_log().replace('cycle=32 byte=2e','cycle=32 byte=2f'),False)
    def test_wrong_read_value(self):
        self.verify(fake_log().replace('READ scenario=FI_P400_0121 target=BUF_0017 cycle=61 byte=2f',
                                       'READ scenario=FI_P400_0121 target=BUF_0017 cycle=61 byte=2e'),False)
    def test_wrong_mid_slot(self):
        self.verify(fake_log().replace('MID_WINDOW scenario=FI_P400_0121 target=BUF_0017 cycle=63 slot=3',
                                       'MID_WINDOW scenario=FI_P400_0121 target=BUF_0017 cycle=63 slot=0'),False)
    def test_wrong_top_slot(self):
        self.verify(fake_log().replace('TOP_MAC scenario=FI_P400_0121 target=BUF_0017 cycle=92 slot=0',
                                       'TOP_MAC scenario=FI_P400_0121 target=BUF_0017 cycle=92 slot=3'),False)
    def test_mid_mac_missing(self):
        self.verify('\n'.join(x for x in fake_log().splitlines() if not x.startswith('FI_V3B_BUF0017_MID_MAC '))+'\n',False)
    def test_top_mac_duplicate(self):
        s=fake_log();line=next(x for x in s.splitlines() if x.startswith('FI_V3B_BUF0017_TOP_MAC '));
        self.verify(s+line+'\n',False)
    def test_wrong_mid_timeline(self):
        self.verify(fake_log().replace('MID_SHIFT scenario=FI_P400_0121 target=BUF_0017 cycle=62',
                                       'MID_SHIFT scenario=FI_P400_0121 target=BUF_0017 cycle=64'),False)
    def test_wrong_top_row_stride(self):
        self.verify(fake_log().replace('TOP_READ scenario=FI_P400_0121 target=BUF_0017 cycle=89',
                                       'TOP_READ scenario=FI_P400_0121 target=BUF_0017 cycle=90'),False)
    def test_overwrite_missing(self):
        self.verify(fake_log().replace('source_overwrites=1','source_overwrites=0'),False)
    def test_secondary_overwrite_missing(self):
        self.verify(fake_log().replace('secondary_overwrites=1','secondary_overwrites=0'),False)
    def test_missing_summary(self):
        self.verify('\n'.join(x for x in fake_log().splitlines() if not x.startswith('FI_V3B_BUF0017_SUMMARY '))+'\n',False)
    def test_injection_mismatch(self):
        self.verify(fake_log().replace('requested=37','requested=38'),False)
    def test_marker_failure(self):
        self.verify(fake_log()+'FI_V3B_BUF0017_TIMELINE_INCOMPLETE\n',False)
    def test_duplicate_fi_result(self):
        s=fake_log();line=next(x for x in s.splitlines() if x.startswith('FI_RESULT_V2,'))
        self.verify(s+line+'\n',False)
    def test_target_identity_mismatch(self):
        row=dict(ROW,target_id='BUF_0018');self.verify(fake_log(),False,row=row)
    def test_case_line_order(self):
        s=fake_log().splitlines();s[5],s[6]=s[6],s[5]
        self.verify('\n'.join(s)+'\n',False)
    def test_missing_stimulus_completion(self):
        self.verify(fake_log().replace(',784,784,784,169,25,',',784,783,784,169,25,'),False)

if __name__=='__main__':unittest.main()
