#!/usr/bin/env python3
"""Positive and negative evidence-gating tests for isolated FC accumulator V3b."""
from __future__ import annotations
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(HERE.parents[1]/'scripts'))
from run_fcacc_v3b import (FROZEN_HASHES,TARGETS,EXPECTED,SELECTED_IDS,
    TB_REL,PROBE_TB_SHA256,V3B_SNAPSHOT,COLUMNS,
    validate_frozen_inputs,selected_fcacc,parse_consumer)
from pilot400_common import load_plan, verify_manifest, sha256
from pilot400_direct_xsim import classify_elaboration
from run_mem1_v3b import override_prj
ROOT=HERE.parents[2]


def row_for(sid='FI_P400_0341'):
    target,offset=EXPECTED[sid]
    return {'scenario_id':sid,'sample_id':'IMG0000','target_id':target,
            'target_class':'DAT','bit_index':'0','injection_offset':offset}


def fake_log(row=None,before=0x12345678,golden=0x00000910,src=None,
             inject=None,bad_at=None,bit=0):
    if row is None:row=row_for()
    sid=row['scenario_id'];target=row['target_id'];lane=TARGETS[target]
    if inject is None:inject=int(row['injection_offset'])
    if src is None:src=inject-1
    after=before^1
    delta=0xffffffff if before&1 else 1
    expected=(golden+delta)&0xffffffff
    final_cycle=1340
    start_counter=24-(final_cycle-inject)
    assert 0<=start_counter<24
    count=25-start_counter
    result=[sid,'IMG0000',target,'DAT',bit,inject,inject,inject,
            1,1,1345,784,784,784,169,25,1,1,1,1,0,0,1,0,'FC','WRONG_LOGITS_SAME_LABEL']
    data=[
      'FI_DISABLED_CONTROL_PASS latency=1342 pool1=676 pool2=200 logits=10 prediction=0',
      f'FI_APPLIED scenario={sid} target={target} class=DAT bit={bit} old={before&1} new={after&1} requested={inject}',
      f'FI_V3B_FCACC_SOURCE scenario={sid} target={target} class={lane} cycle={src} acc={before:08x}',
      f'FI_V3B_FCACC_INJECT scenario={sid} target={target} class={lane} cycle={inject} bit={bit} before={before:08x} after={after:08x}',
    ]
    acc=after
    for i in range(count):
        cycle=inject+i;counter=start_counter+i
        dot=0 if i<count-1 else (expected-acc)&0xffffffff
        post=(acc+dot)&0xffffffff
        data.append(f'FI_V3B_FCACC_ACCEPT scenario={sid} target={target} class={lane} cycle={cycle} seq={i} counter={counter} pre={acc:08x} dot={dot:08x} post={post:08x} final={int(i==count-1)}')
        acc=post
    assert acc==expected
    data += [
      f'FI_V3B_FCACC_LOGIT scenario={sid} target={target} class={lane} cycle={final_cycle} actual={acc:08x} golden={golden:08x} delta={delta:08x}',
      f'FI_V3B_FCACC_SUMMARY scenario={sid} target={target} class={lane} source={src} inject={inject} first={inject} final={final_cycle} accepts={count} before={before:08x} after={after:08x} value={acc:08x} expected={expected:08x}',
      'FI_RESULT_V2,'+','.join(str(x) for x in result),
      'CNN_PEP FI CAMPAIGN V2: PASS (mechanism/log integrity; outcome may be harmful)',
    ]
    return '\n'.join(data)+'\n'


class FCAccTests(unittest.TestCase):
    def verify(self,txt,good=True,row=None,reference=None):
        row=row or row_for()
        with tempfile.TemporaryDirectory() as d:
            log=Path(d)/'run.log';log.write_text(txt,encoding='utf-8')
            ref=None
            if reference is not None:
                ref=Path(d)/'reference.log';ref.write_text(reference,encoding='utf-8')
            if good:
                r=parse_consumer(log,row,ref)
                self.assertEqual(set(r),set(COLUMNS))
                self.assertEqual(r['fc_class'],str(TARGETS[row['target_id']]))
                self.assertEqual(r['consumer_proof'],
                    'VERIFIED_FC_ACCUMULATOR_READ_DOT8_UPDATES_AND_FINAL_LOGIT_DELTA')
                self.assertEqual(r['outcome'],'WRONG_LOGITS_SAME_LABEL')
                self.assertEqual(r['final_cycle'],'1340')
                self.assertEqual(r['logit_delta'],'00000001')
            else:
                with self.assertRaises(ValueError):parse_consumer(log,row,ref)

    def test_frozen_inputs_and_exact_isolated_tb(self):
        validate_frozen_inputs(ROOT)
        self.assertEqual(sha256(ROOT/TB_REL),PROBE_TB_SHA256)
        self.assertNotEqual(PROBE_TB_SHA256,FROZEN_HASHES['fi/tb/tb_cnn_mnist_fi_pilot400.v'])
    def test_frozen_four_cases(self):
        plan=load_plan(ROOT/'fi/config/pilot400_targets.csv',ROOT/'fi/config/fi_targets_candidates_v2.csv')
        rows=selected_fcacc(verify_manifest(ROOT/'fi/scenarios/pilot400.csv',plan))
        self.assertEqual([r['scenario_id'] for r in rows],list(SELECTED_IDS))
        self.assertEqual({r['target_id'] for r in rows},set(TARGETS))
    def test_reject_duplicate_scenarios(self):
        plan=load_plan(ROOT/'fi/config/pilot400_targets.csv',ROOT/'fi/config/fi_targets_candidates_v2.csv')
        rows=selected_fcacc(verify_manifest(ROOT/'fi/scenarios/pilot400.csv',plan))
        with self.assertRaises(ValueError):selected_fcacc(rows+rows[:1])
    def test_compile_prj_only_replaces_fi_tb(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'tb_cnn_mnist_fi_pilot400.v';p.write_text('module fake; endmodule')
            src='verilog xil_defaultlib "C:/RTL/fc.v"\nverilog xil_defaultlib "C:/TB/tb_cnn_mnist_fi_pilot400.v"\n'
            result=override_prj(src,p)
            self.assertIn(p.resolve().as_posix(),result)
            self.assertIn('C:/RTL/fc.v',result)
            with self.assertRaises(ValueError):override_prj(src+src,p)
    def test_correct_xelab_marker(self):
        with tempfile.TemporaryDirectory() as d:
            snap=Path(d)/'xsim.dir'/V3B_SNAPSHOT;snap.mkdir(parents=True)
            (snap/'xsimk.dll').write_bytes(b'kernel')
            log='Running: xelab.exe --debug typical\nStarting static elaboration\nCompleted static elaboration\nBuilt simulation snapshot '+V3B_SNAPSHOT+'\n'
            self.assertEqual(classify_elaboration(log,snap,None,expected_snapshot=V3B_SNAPSHOT),'FULL_BUILD')
            with self.assertRaises(ValueError):
                classify_elaboration(log.replace(V3B_SNAPSHOT,'wrong_behav'),snap,None,expected_snapshot=V3B_SNAPSHOT)
    def test_valid_both_targets_and_offsets(self):
        for sid in SELECTED_IDS:
            with self.subTest(sid=sid):self.verify(fake_log(row_for(sid)),row=row_for(sid))
    def test_valid_negative_logit_delta(self):
        # x bit0=1 -> XOR to 0, hence final output is golden-1.
        row=row_for()
        x=fake_log(row,before=0x12345679)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'run.log';p.write_text(x)
            r=parse_consumer(p,row)
            self.assertEqual(r['logit_delta'],'ffffffff')
    def test_original_reference_equivalent(self):
        t=fake_log();self.verify(t,reference=t)
    def test_original_reference_changed_result(self):
        t=fake_log();self.verify(t,False,reference=t.replace('FC,WRONG_LOGITS_SAME_LABEL','NONE,FINAL_OUTPUT_MATCH'))
    def test_missing_control(self):self.verify(fake_log().replace('FI_DISABLED_CONTROL_PASS','FI_DISABLED_CONTROL_FAIL'),False)
    def test_missing_inject(self):self.verify('\n'.join(x for x in fake_log().splitlines() if not x.startswith('FI_V3B_FCACC_INJECT'))+'\n',False)
    def test_missing_logit(self):self.verify('\n'.join(x for x in fake_log().splitlines() if not x.startswith('FI_V3B_FCACC_LOGIT'))+'\n',False)
    def test_missing_summary(self):self.verify('\n'.join(x for x in fake_log().splitlines() if not x.startswith('FI_V3B_FCACC_SUMMARY'))+'\n',False)
    def test_duplicate_accept(self):
        t=fake_log();ln=next(x for x in t.splitlines() if x.startswith('FI_V3B_FCACC_ACCEPT'))
        self.verify(t+ln+'\n',False)
    def test_duplicate_fi_result(self):
        t=fake_log();ln=next(x for x in t.splitlines() if x.startswith('FI_RESULT_V2,'))
        self.verify(t+ln+'\n',False)
    def test_extra_unknown_marker(self):self.verify(fake_log()+'FI_V3B_FCACC_NOT_PROVED\n',False)
    def test_wrong_source_cycle(self):self.verify(fake_log(src=1325),False)
    def test_wrong_source_value(self):
        self.verify(fake_log().replace('acc=12345678','acc=12345679',1),False)
    def test_wrong_xor(self):
        self.verify(fake_log().replace('before=12345678 after=12345679','before=12345678 after=1234567a'),False)
    def test_wrong_injected_cycle(self):
        self.verify(fake_log(inject=1326),False)
    def test_wrong_applied_bit(self):
        self.verify(fake_log().replace('old=0 new=1','old=1 new=0',1),False)
    def test_wrong_class(self):
        self.verify(fake_log().replace('class=0 cycle=1325','class=5 cycle=1325'),False)
    def test_wrong_scenario(self):
        self.verify(fake_log().replace('target=DAT_0007 class=0 cycle=1325','target=DAT_0008 class=0 cycle=1325'),False)
    def test_missing_accept(self):
        t=fake_log();ln=next(x for x in t.splitlines() if 'seq=5 ' in x and x.startswith('FI_V3B_FCACC_ACCEPT'))
        self.verify(t.replace(ln+'\n',''),False)
    def test_accept_wrong_sequence(self):
        self.verify(fake_log().replace('seq=1 counter=10','seq=7 counter=10'),False)
    def test_accept_wrong_counter(self):
        self.verify(fake_log().replace('seq=1 counter=10','seq=1 counter=11'),False)
    def test_accept_wrong_cycle(self):
        self.verify(fake_log().replace('cycle=1326 seq=1','cycle=1328 seq=1'),False)
    def test_accept_wrong_pre(self):
        self.verify(fake_log().replace('counter=10 pre=12345679','counter=10 pre=12345678'),False)
    def test_accept_wrong_dot_post(self):
        self.verify(fake_log().replace('post=12345679 final=0','post=1234567a final=0',1),False)
    def test_final_flag_early(self):
        self.verify(fake_log().replace('seq=0 counter=9 pre=12345679 dot=00000000 post=12345679 final=0',
                                       'seq=0 counter=9 pre=12345679 dot=00000000 post=12345679 final=1'),False)
    def test_final_flag_missing(self):
        self.verify(fake_log().replace('post=00000911 final=1','post=00000911 final=0'),False)
    def test_wrong_golden(self):self.verify(fake_log().replace('golden=00000910','golden=00000912'),False)
    def test_wrong_delta(self):self.verify(fake_log().replace('delta=00000001','delta=ffffffff'),False)
    def test_wrong_final_logit(self):
        self.verify(fake_log().replace('actual=00000911','actual=00000910'),False)
    def test_wrong_summary_count(self):
        self.verify(re.sub(r' accepts=\d+ before=', ' accepts=1 before=',fake_log()),False)
    def test_wrong_summary_value(self):
        self.verify(fake_log().replace('value=00000911 expected=00000911',
                                       'value=00000910 expected=00000911'),False)
    def test_wrong_case_bit(self):
        self.verify(fake_log(),False,row=dict(row_for(),bit_index='1'))
    def test_wrong_case_offset(self):
        self.verify(fake_log(),False,row=dict(row_for(),injection_offset='1326'))
    def test_wrong_case_sample(self):
        self.verify(fake_log(),False,row=dict(row_for(),sample_id='IMG0001'))
    def test_probe_byte_mutation_fails_gate(self):
        with tempfile.TemporaryDirectory() as d:
            temp=Path(d)
            for rel in (*FROZEN_HASHES,str(TB_REL)):
                target=temp/rel;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes((ROOT/rel).read_bytes())
            tb=temp/TB_REL
            tb.write_text(tb.read_text().replace('FCACC_FINAL_LOGIT_NOT_PROVED',
                                                 'FCACC_FINAL_LOGIT_SILENCED'))
            with self.assertRaises(ValueError):validate_frozen_inputs(temp)

if __name__=='__main__':unittest.main()
