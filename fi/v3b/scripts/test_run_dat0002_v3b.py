#!/usr/bin/env python3
"""Negative/positive offline tests for V3b DAT_0002 Pool1 arithmetic-input acceptance."""
from __future__ import annotations
import tempfile
import unittest
import sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(HERE.parents[1]/'scripts'))
from run_dat0002_v3b import (TARGET_ID,SELECTED_IDS,V3B_SNAPSHOT,COLUMNS,TB_REL,
     validate_frozen_inputs,selected_dat0002,parse_consumer)
from pilot400_common import sha256,load_plan,verify_manifest
from pilot400_direct_xsim import classify_elaboration
from run_mem1_v3b import override_prj

ROOT=HERE.parents[2]
ROW={'scenario_id':'FI_P400_0301','sample_id':'IMG0000','target_id':'DAT_0002',
     'target_class':'DAT','bit_index':'0','injection_offset':'400'}


def fake_log(row=ROW,original=0x12345678,x=0,y=0,produce_delta=1,
             window_delta=26,after=None,overwrites=0):
    if after is None:after=original^1
    sid=row['scenario_id'];offset=int(row['injection_offset'])
    inject=offset+3;prod=inject-produce_delta
    slot=2*(y%2)+(x%2); window=inject+window_delta
    if x%2 and y%2:window=inject
    consume=window+1
    lines=[
        'FI_DISABLED_CONTROL_PASS latency=1342 pool1=676 pool2=200 logits=10 prediction=0',
        f'FI_APPLIED scenario={sid} target=DAT_0002 class=DAT bit=0 old=0 new=1 requested={inject}',
        f'FI_V3B_DAT0002_PRODUCE scenario={sid} target=DAT_0002 cycle={prod} word={original:08x}',
        f'FI_V3B_DAT0002_INJECT scenario={sid} target=DAT_0002 cycle={inject} bit=0 before={original:08x} after={after:08x}',
        f'FI_V3B_DAT0002_ACCEPT scenario={sid} target=DAT_0002 cycle={inject} x={x} y={y} word={after:08x}',
        f'FI_V3B_DAT0002_STORE scenario={sid} target=DAT_0002 cycle={inject} x={x} word={after:08x}',
        f'FI_V3B_DAT0002_WINDOW scenario={sid} target=DAT_0002 cycle={window} endx={x|1} endy={y|1} slot={slot} word={after:08x}',
        f'FI_V3B_DAT0002_POOL_CONSUME scenario={sid} target=DAT_0002 cycle={consume} slot={slot} word={after:08x}',
        f'FI_V3B_DAT0002_SUMMARY scenario={sid} target=DAT_0002 inject={inject} accept={inject} store={inject} window={window} consume={consume} overwrites={overwrites} x={x} y={y} slot={slot} before={original:08x} after={after:08x}',
        'FI_RESULT_V2,'+','.join(map(str,[sid,'IMG0000','DAT_0002','DAT',0,offset,inject,inject,
           1,1,1345,784,784,784,169,25,1,1,1,1,2,0,0,0,'POOL1','FINAL_OUTPUT_MATCH'])),
        'CNN_PEP FI CAMPAIGN V2: PASS (mechanism/log integrity; outcome may be harmful)',
    ]
    return '\n'.join(lines)+'\n'

class Dat0002Tests(unittest.TestCase):
    def check(self,log,valid=True,row=ROW,reference=None):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'scenario.log';path.write_text(log,encoding='utf-8')
            ref=None
            if reference is not None:
                ref=Path(tmp)/'baseline.log';ref.write_text(reference,encoding='utf-8')
            if valid:
                parsed=parse_consumer(path,row,ref)
                self.assertEqual(parsed['consumer_proof'],'VERIFIED_CONV1_QUANT_TO_POOL1_MAX_INPUT')
                self.assertEqual(parsed['outcome'],'FINAL_OUTPUT_MATCH')
                self.assertEqual(set(parsed),set(COLUMNS))
            else:
                with self.assertRaises(ValueError):parse_consumer(path,row,ref)

    def test_frozen_sources(self):
        validate_frozen_inputs(ROOT)
        self.assertNotEqual(sha256(ROOT/TB_REL),sha256(ROOT/'fi/tb/tb_cnn_mnist_fi_pilot400.v'))

    def test_selected_pair(self):
        plan=load_plan(ROOT/'fi/config/pilot400_targets.csv',ROOT/'fi/config/fi_targets_candidates_v2.csv')
        rows=verify_manifest(ROOT/'fi/scenarios/pilot400.csv',plan)
        selected=selected_dat0002(rows)
        self.assertEqual([(r['scenario_id'],r['bit_index'],r['injection_offset']) for r in selected],
                         [('FI_P400_0301','0','400'),('FI_P400_0302','0','600')])

    def test_snapshot_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'xsim.dir'/V3B_SNAPSHOT;path.mkdir(parents=True)
            (path/'xsimk.dll').write_bytes(b'kernel')
            marker=f'Running: xelab.exe\nStarting static elaboration\nCompleted static elaboration\nBuilt simulation snapshot {V3B_SNAPSHOT}\n'
            self.assertEqual(classify_elaboration(marker,path,None,expected_snapshot=V3B_SNAPSHOT),'FULL_BUILD')
            with self.assertRaises(ValueError):classify_elaboration(marker.replace(V3B_SNAPSHOT,'wrong'),path,None,expected_snapshot=V3B_SNAPSHOT)

    def test_override_one_tb_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'tb_cnn_mnist_fi_pilot400.v';path.write_text('module fake; endmodule')
            old='verilog xil_defaultlib "C:/Simulation/tb_cnn_mnist_fi_pilot400.v"\nverilog xil_defaultlib "C:/Simulation/pool1_buf.v"\n'
            new=override_prj(old,path)
            self.assertIn(path.resolve().as_posix(),new)
            self.assertIn('C:/Simulation/pool1_buf.v',new)
            self.assertEqual(new.count('tb_cnn_mnist_fi_pilot400.v'),1)
            with self.assertRaises(ValueError):override_prj(old+old,path)

    def test_0301_even_even(self):self.check(fake_log())
    def test_0302_odd_odd_same_cycle_emit(self):
        row=dict(ROW,scenario_id='FI_P400_0302',injection_offset='600')
        self.check(fake_log(row=row,x=11,y=21),row=row)
    def test_position_topright(self):self.check(fake_log(x=7,y=6,window_delta=26))
    def test_position_bottomleft(self):self.check(fake_log(x=4,y=5,window_delta=1))
    def test_strict_v3a_equivalence(self):self.check(fake_log(),reference=fake_log())
    def test_reference_mismatch(self):
        orig=fake_log();self.check(orig,False,reference=orig.replace('POOL1,FINAL_OUTPUT_MATCH','FC,WRONG_LOGITS_SAME_LABEL'))
    def test_wrong_xor(self):self.check(fake_log(after=0x1234567a),False)
    def test_injection_not_consumer_edge(self):
        s=fake_log().replace('ACCEPT scenario=FI_P400_0301 target=DAT_0002 cycle=403',
                             'ACCEPT scenario=FI_P400_0301 target=DAT_0002 cycle=404')
        self.check(s,False)
    def test_producer_not_before_injection(self):self.check(fake_log(produce_delta=0),False)
    def test_missing_producer(self):
        self.check('\n'.join(l for l in fake_log().splitlines() if not l.startswith('FI_V3B_DAT0002_PRODUCE'))+'\n',False)
    def test_missing_consumer(self):
        self.check('\n'.join(l for l in fake_log().splitlines() if not l.startswith('FI_V3B_DAT0002_POOL_CONSUME'))+'\n',False)
    def test_duplicate_consumer(self):
        s=fake_log();line=next(l for l in s.splitlines() if l.startswith('FI_V3B_DAT0002_POOL_CONSUME '))
        self.check(s+line+'\n',False)
    def test_wrong_accept_word(self):
        self.check(fake_log().replace('y=0 word=12345679','y=0 word=12345678'),False)
    def test_wrong_stored_word(self):
        self.check(fake_log().replace('STORE scenario=FI_P400_0301 target=DAT_0002 cycle=403 x=0 word=12345679',
                                      'STORE scenario=FI_P400_0301 target=DAT_0002 cycle=403 x=0 word=12345678'),False)
    def test_wrong_window_slot(self):self.check(fake_log().replace('endy=1 slot=0','endy=1 slot=1'),False)
    def test_wrong_window_data(self):
        self.check(fake_log().replace('endy=1 slot=0 word=12345679','endy=1 slot=0 word=12345678'),False)
    def test_wrong_consume_data(self):
        self.check(fake_log().replace('POOL_CONSUME scenario=FI_P400_0301 target=DAT_0002 cycle=430 slot=0 word=12345679',
                                      'POOL_CONSUME scenario=FI_P400_0301 target=DAT_0002 cycle=430 slot=0 word=12345678'),False)
    def test_wrong_timeline(self):self.check(fake_log().replace('cycle=430 slot=0','cycle=432 slot=0'),False)
    def test_overwrites(self):self.check(fake_log(overwrites=1),False)
    def test_reordered_lines(self):
        s=fake_log().splitlines();s[5],s[6]=s[6],s[5]
        self.check('\n'.join(s)+'\n',False)
    def test_wrong_identity(self):
        row=dict(ROW,scenario_id='FI_P400_0001');self.check(fake_log(),False,row=row)
    def test_wrong_sample(self):
        row=dict(ROW,sample_id='IMG0001');self.check(fake_log(),False,row=row)
    def test_failed_marker(self):self.check(fake_log()+'FI_V3B_DAT0002_POOL_CONSUME_NOT_PROVED\n',False)
    def test_missing_fi_result(self):self.check(fake_log().replace('FI_RESULT_V2,','FI_RESULT_V1,'),False)

if __name__=='__main__':unittest.main()
