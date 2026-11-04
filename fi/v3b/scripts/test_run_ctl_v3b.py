#!/usr/bin/env python3
"""Adversarial V3b CTL log and frozen-source contract tests (no Vivado)."""
from __future__ import annotations
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(HERE.parents[1]/'scripts'))
from run_ctl_v3b import (TARGETS,EXPECTED,SELECTED_IDS,V3B_SNAPSHOT,TB_REL,COLUMNS,
                        PROBE_TB_SHA256,validate_frozen_inputs,selected_ctl,parse_consumer)
from pilot400_common import load_plan, verify_manifest, sha256
from pilot400_direct_xsim import classify_elaboration
from run_mem1_v3b import override_prj
ROOT=HERE.parents[2]


def row_for(sid):
    target,offset=EXPECTED[sid]
    return {'scenario_id':sid,'sample_id':'IMG0000','target_id':target,
            'target_class':'CTL','bit_index':'0','injection_offset':offset}


def example_event(row, before=None):
    sid=row['scenario_id'];tid=row['target_id'];inject=int(row['injection_offset'])
    if tid=='CTL_0012':
        before=0x50 if before is None else before
        after=before^1
        event=f'FI_V3B_CTL_MEM1 scenario={sid} target={tid} cycle={inject} old={before:02x} new={after:02x} valid=1 start=0 data=1234abcd post={after+1} write=1'
    elif tid=='CTL_0019':
        before=11 if before is None else before;after=before^1
        event=f'FI_V3B_CTL_FC scenario={sid} target={tid} cycle={inject} old={before:02x} new={after:02x} busy=1 valid=1 rom={after} preacc=00000500 dot=00000001 postacc=00000501 postcnt={after+1} final=0'
    elif tid=='CTL_0026':
        before=20 if before is None else before;after=before^1
        event=f'FI_V3B_CTL_CONV1 scenario={sid} target={tid} cycle={inject} old={before:02x} new={after:02x} valid=1 start=0 y=3 pixel=12 prev=ab postx={after+1} posty=3 window=1'
    else:
        before=1 if before is None else before;after=before^1
        if after==0:post=0;oval=0
        elif after==1:post=1;oval=0
        elif after==2:post=3;oval=0
        else:post=3;oval=1
        event=f'FI_V3B_CTL_CONV2 scenario={sid} target={tid} cycle={inject} old={before:02x} new={after:02x} start=0 valid=1 count=80 x=3 y=4 post={post} oval={oval}'
    return before,after,event


def fake_log(row, before=None):
    sid=row['scenario_id'];tid=row['target_id'];inject=int(row['injection_offset'])
    old,new,event=example_event(row,before)
    result=[sid,'IMG0000',tid,'CTL',0,inject,inject,inject,
            1,1,1345,784,784,784,169,25,1,1,1,1,0,0,1,0,'FC','WRONG_LOGITS_SAME_LABEL']
    return '\n'.join([
        'FI_DISABLED_CONTROL_PASS latency=1342 pool1=676 pool2=200 logits=10 prediction=0',
        f'FI_APPLIED scenario={sid} target={tid} class=CTL bit=0 old={old&1} new={new&1} requested={inject}',
        event,
        f'FI_V3B_CTL_SUMMARY scenario={sid} target={tid} inject={inject} uses=1 old={old:02x} new={new:02x}',
        'FI_RESULT_V2,'+','.join(str(v) for v in result),
        'CNN_PEP FI CAMPAIGN V2: PASS (mechanism/log integrity; outcome may be harmful)',
    ])+'\n'


class CTLContractTests(unittest.TestCase):
    def verify(self,text,row,expect=True,reference=None):
        with tempfile.TemporaryDirectory() as temp:
            log=Path(temp)/'log.txt';log.write_text(text,encoding='utf-8')
            ref=None
            if reference is not None:
                ref=Path(temp)/'original.txt';ref.write_text(reference,encoding='utf-8')
            if expect:
                info=parse_consumer(log,row,ref)
                self.assertEqual(set(info),set(COLUMNS))
                self.assertEqual(info['consumer_kind'],TARGETS[row['target_id']])
                self.assertEqual(info['consumer_cycle'],row['injection_offset'])
                self.assertEqual(info['consumer_proof'],'VERIFIED_PRE_NBA_CONTROL_DECISION_AND_POST_NBA_TRANSITION')
            else:
                with self.assertRaises(ValueError):parse_consumer(log,row,ref)

    def test_frozen_tb_and_monitor_isolated(self):
        validate_frozen_inputs(ROOT)
        self.assertEqual(sha256(ROOT/TB_REL),PROBE_TB_SHA256)
        self.assertNotEqual(sha256(ROOT/TB_REL),sha256(ROOT/'fi/tb/tb_cnn_mnist_fi_pilot400.v'))

    def test_frozen_8_scenario_selection(self):
        p=load_plan(ROOT/'fi/config/pilot400_targets.csv',ROOT/'fi/config/fi_targets_candidates_v2.csv')
        m=verify_manifest(ROOT/'fi/scenarios/pilot400.csv',p)
        rows=selected_ctl(m)
        self.assertEqual(tuple(x['scenario_id'] for x in rows),SELECTED_IDS)
        self.assertEqual(len(rows),8)
        self.assertEqual({r['target_id'] for r in rows},set(TARGETS))
        with self.assertRaises(ValueError):selected_ctl(m+[rows[0]])

    def test_all_8_valid(self):
        for sid in SELECTED_IDS:
            with self.subTest(sid=sid):
                row=row_for(sid);self.verify(fake_log(row),row)

    def test_case_specific_additional_branches(self):
        for sid,value in [('FI_P400_0021',168),('FI_P400_0041',22),
                          ('FI_P400_0061',26),('FI_P400_0082',3),
                          ('FI_P400_0081',2)]:
            row=row_for(sid)
            text=fake_log(row,value)
            if row['target_id']=='CTL_0012':
                # 168 xor 1 = 169: write disabled by DEPTH guard, ptr held.
                text=text.replace('post=170 write=1','post=169 write=0')
            if row['target_id']=='CTL_0026':
                # 26 xor 1 = 27: x/y wrap and output-valid remains asserted.
                text=text.replace('postx=28 posty=3','postx=0 posty=4')
            if row['target_id']=='CTL_0019':
                # 22 xor 1 = 23, still accumulation case.
                pass
            self.verify(text,row)

    def test_original_v3a_result_unchanged(self):
        for sid in SELECTED_IDS:
            row=row_for(sid);log=fake_log(row)
            self.verify(log,row,reference=log)
            bad=log.replace('FC,WRONG_LOGITS_SAME_LABEL','NONE,FINAL_OUTPUT_MATCH')
            self.verify(log,row,False,reference=bad)

    def test_missing_marker(self):
        for sid in SELECTED_IDS:
            row=row_for(sid)
            for prefix in ('FI_V3B_CTL_', 'FI_V3B_CTL_SUMMARY'):
                txt=fake_log(row)
                if prefix=='FI_V3B_CTL_':
                    txt='\n'.join(line for line in txt.splitlines() if not line.startswith('FI_V3B_CTL_') or line.startswith('FI_V3B_CTL_SUMMARY'))+'\n'
                else:
                    txt='\n'.join(line for line in txt.splitlines() if not line.startswith(prefix))+'\n'
                self.verify(txt,row,False)

    def test_extra_or_duplicate_marker(self):
        for sid in SELECTED_IDS:
            row=row_for(sid);txt=fake_log(row)
            extra=next(l for l in txt.splitlines() if l.startswith('FI_V3B_CTL_') and not l.startswith('FI_V3B_CTL_SUMMARY'))
            self.verify(txt+extra+'\n',row,False)
            self.verify(txt+'FI_V3B_CTL_NOT_A_REAL_EVENT\n',row,False)

    def test_wrong_bit_word_and_readback(self):
        for sid in SELECTED_IDS:
            row=row_for(sid);txt=fake_log(row)
            old,new,event=example_event(row)
            self.verify(txt.replace(f'new={new:02x}',f'new={old:02x}'),row,False)
            self.verify(txt.replace(f'old={old&1} new={new&1} requested=',f'old={new&1} new={old&1} requested='),row,False)
            self.verify(txt.replace('class=CTL bit=0','class=CTL bit=1'),row,False)

    def test_wrong_scenario_or_timing(self):
        for sid in SELECTED_IDS:
            row=row_for(sid);txt=fake_log(row)
            self.verify(txt.replace('cycle='+row['injection_offset'],'cycle=999'),row,False)
            self.verify(txt.replace('uses=1','uses=0'),row,False)
            self.verify(txt.replace('FI_APPLIED scenario='+sid,'FI_APPLIED scenario=WRONG'),row,False)

    def test_mem1_wrong_pointer_or_enable(self):
        row=row_for('FI_P400_0021');txt=fake_log(row)
        self.verify(txt.replace('post=82','post=81'),row,False)
        self.verify(txt.replace('valid=1 start=0','valid=0 start=0'),row,False)
        self.verify(txt.replace('write=1','write=0'),row,False)

    def test_fc_wrong_rom_dot_counter_final(self):
        row=row_for('FI_P400_0041');txt=fake_log(row)
        for a,b in [('rom=10','rom=11'),('postacc=00000501','postacc=00000500'),
                    ('postcnt=11','postcnt=12'),('final=0','final=1'),('busy=1','busy=0')]:
            with self.subTest(a=a):self.verify(txt.replace(a,b),row,False)

    def test_conv1_wrong_address_window_y(self):
        row=row_for('FI_P400_0061');txt=fake_log(row)
        for a,b in [('postx=22','postx=21'),('posty=3','posty=4'),
                    ('window=1','window=0'),('valid=1','valid=0')]:
            with self.subTest(a=a):self.verify(txt.replace(a,b),row,False)

    def test_conv2_wrong_branch(self):
        row=row_for('FI_P400_0081');txt=fake_log(row)
        for a,b in [('post=0','post=1'),('oval=0','oval=1')]:
            with self.subTest(a=a):self.verify(txt.replace(a,b),row,False)
        row=row_for('FI_P400_0082');txt=fake_log(row,3)
        for a,b in [('post=3','post=2'),('oval=0','oval=1')]:
            with self.subTest(a=a):self.verify(txt.replace(a,b),row,False)

    def test_missing_control_and_duplicate_result(self):
        row=row_for('FI_P400_0021');txt=fake_log(row)
        self.verify(txt.replace('FI_DISABLED_CONTROL_PASS','FI_DISABLED_CONTROL_FAIL'),row,False)
        line=next(l for l in txt.splitlines() if l.startswith('FI_RESULT_V2,'))
        self.verify(txt+line+'\n',row,False)
        self.verify(txt+'ERROR: synthetic\n',row,False)

    def test_wrong_snapshot_name(self):
        with tempfile.TemporaryDirectory() as d:
            snap=Path(d)/'xsim.dir'/V3B_SNAPSHOT;snap.mkdir(parents=True)
            (snap/'xsimk.dll').write_bytes(b'kernel')
            log='Running: xelab.exe --debug typical\nStarting static elaboration\nCompleted static elaboration\nBuilt simulation snapshot '+V3B_SNAPSHOT+'\n'
            self.assertEqual(classify_elaboration(log,snap,None,expected_snapshot=V3B_SNAPSHOT),'FULL_BUILD')
            with self.assertRaises(ValueError):
                classify_elaboration(log.replace(V3B_SNAPSHOT,'old_behav'),snap,None,expected_snapshot=V3B_SNAPSHOT)

    def test_source_list_replaces_only_tb(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'tb_cnn_mnist_fi_pilot400.v';p.write_text('module tb;endmodule')
            prj='verilog xil_defaultlib "C:/RTL/a.v"\nverilog xil_defaultlib "C:/OLD/tb_cnn_mnist_fi_pilot400.v"\n'
            changed=override_prj(prj,p)
            self.assertIn(p.resolve().as_posix(),changed)
            self.assertIn('C:/RTL/a.v',changed)
            with self.assertRaises(ValueError):override_prj(prj+prj,p)

if __name__=='__main__':unittest.main()
