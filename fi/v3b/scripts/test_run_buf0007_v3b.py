#!/usr/bin/env python3
"""Offline tests for isolated BUF_0007 byte337-to-Conv2 MAC consumer evidence."""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path
import unittest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(HERE.parents[1]/'scripts'))
from run_buf0007_v3b import (FROZEN_HASHES, V3B_SNAPSHOT, COLUMNS,
    validate_frozen_inputs, selected_buf0007, parse_consumer, TB_REL)
from pilot400_common import sha256, load_plan, verify_manifest
from pilot400_direct_xsim import classify_elaboration

REPO=HERE.parents[2]
ROW={'scenario_id':'FI_P400_0101','sample_id':'IMG0000',
     'target_id':'BUF_0007','target_class':'BUF',
     'bit_index':'0','injection_offset':'791'}


def fake_log(row=ROW,orig=0x18,after=None,delta_consume=1,duplicate=None,missing=None,word=None):
    if after is None:after=orig^1
    if word is None:word=(0x12340078|(after<<8))
    inj=int(row['injection_offset'])+3
    times=[inj-1,inj,880,881,1120,1121,1300,1300+delta_consume]
    sid=row['scenario_id'];target=row['target_id'];data=[
       'FI_DISABLED_CONTROL_PASS latency=1342 pool1=676 pool2=200 logits=10 prediction=0',
       f'FI_APPLIED scenario={sid} target={target} class=BUF bit=0 old=0 new=1 requested={inj}',
       f'FI_V3B_BUF0007_WRITE scenario={sid} target={target} cycle={times[0]} byte={orig:02x}',
       f'FI_V3B_BUF0007_INJECT scenario={sid} target={target} cycle={times[1]} bit=0 before={orig:02x} after={after:02x}',
       f'FI_V3B_BUF0007_DRAIN scenario={sid} target={target} cycle={times[2]} word={word:08x}',
       f'FI_V3B_BUF0007_MEM_WRITE scenario={sid} target={target} cycle={times[3]} word={word:08x}',
       f'FI_V3B_BUF0007_MEM_READ scenario={sid} target={target} cycle={times[4]} word={word:08x}',
       f'FI_V3B_BUF0007_CAPTURE scenario={sid} target={target} cycle={times[5]} byte337={after:02x}',
       f'FI_V3B_BUF0007_WINDOW scenario={sid} target={target} cycle={times[6]} byte={after:02x}',
       f'FI_V3B_BUF0007_CALC_CONSUME scenario={sid} target={target} cycle={times[7]} byte={after:02x}',
       (f'FI_V3B_BUF0007_SUMMARY scenario={sid} target={target} '
        f'write={times[0]} inject={times[1]} drain={times[2]} memwrite={times[3]} '
        f'memread={times[4]} capture={times[5]} window={times[6]} consume={times[7]} '
        f'overwrites=0 original={orig:02x} corrupt={after:02x} word={word:08x}'),
       'FI_RESULT_V2,'+','.join(map(str,[sid,'IMG0000',target,'BUF',0,row['injection_offset'],inj,inj,
            1,1,1345,784,784,784,169,25,1,1,1,1,2,0,0,0,'POOL1','FINAL_OUTPUT_MATCH'])),
       'CNN_PEP FI CAMPAIGN V2: PASS (mechanism/log integrity; outcome may be harmful)'
    ]
    if missing:data=[x for x in data if not x.startswith('FI_V3B_BUF0007_'+missing+' ')]
    if duplicate:
        ln=next(x for x in data if x.startswith('FI_V3B_BUF0007_'+duplicate+' '))
        data.insert(4,ln)
    return '\n'.join(data)+'\n'


class BufferConsumerTests(unittest.TestCase):
    def test_locked_source_unchanged(self):
        validate_frozen_inputs(REPO)
        for name,h in FROZEN_HASHES.items():self.assertEqual(sha256(REPO/name),h)
        self.assertNotEqual(sha256(REPO/TB_REL),FROZEN_HASHES['fi/tb/tb_cnn_mnist_fi_pilot400.v'])

    def test_frozen_two_scenarios(self):
        plan=load_plan(REPO/'fi/config/pilot400_targets.csv',REPO/'fi/config/fi_targets_candidates_v2.csv')
        full=verify_manifest(REPO/'fi/scenarios/pilot400.csv',plan)
        selected=selected_buf0007(full)
        self.assertEqual([(r['scenario_id'],r['bit_index'],r['injection_offset']) for r in selected],
                         [('FI_P400_0101','0','791'),('FI_P400_0102','0','792')])

    def test_snapshot_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'xsim.dir'/V3B_SNAPSHOT;p.mkdir(parents=True)
            (p/'xsimk.dll').write_bytes(b'fake')
            log='Running: xelab.exe\nStarting static elaboration\nCompleted static elaboration\nBuilt simulation snapshot '+V3B_SNAPSHOT+'\n'
            self.assertEqual(classify_elaboration(log,p,None,expected_snapshot=V3B_SNAPSHOT),'FULL_BUILD')
            with self.assertRaises(ValueError):
                classify_elaboration(log.replace(V3B_SNAPSHOT,'tb_cnn_mnist_fi_pilot400_behav'),p,None,expected_snapshot=V3B_SNAPSHOT)

    def check(self,s,valid=True,row=ROW,reference=None):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'scenario.log';log.write_text(s,encoding='utf-8')
            ref=None
            if reference is not None:
                ref=Path(tmp)/'original.log';ref.write_text(reference,encoding='utf-8')
            if valid:
                result=parse_consumer(log,row,ref)
                self.assertEqual(result['consumer_proof'],'VERIFIED_BUF337_TO_CONV2_MAC_CH1_P8')
                self.assertEqual(result['outcome'],'FINAL_OUTPUT_MATCH')
                self.assertEqual(set(result),set(COLUMNS))
            else:
                with self.assertRaises(ValueError):parse_consumer(log,row,ref)

    def test_valid_791(self):self.check(fake_log())
    def test_valid_792(self):
        row=dict(ROW,scenario_id='FI_P400_0102',injection_offset='792')
        self.check(fake_log(row=row),row=row)
    def test_match_v3a_strict(self):
        s=fake_log();self.check(s,reference=s)
    def test_changed_fi_result_rejected(self):
        s=fake_log();self.check(s,valid=False,reference=s.replace('POOL1,FINAL_OUTPUT_MATCH','FC,WRONG_LOGITS_SAME_LABEL'))
    def test_missing_write_rejected(self):self.check(fake_log(missing='WRITE'),False)
    def test_missing_drain_rejected(self):self.check(fake_log(missing='DRAIN'),False)
    def test_missing_mem_read_rejected(self):self.check(fake_log(missing='MEM_READ'),False)
    def test_missing_capture_rejected(self):self.check(fake_log(missing='CAPTURE'),False)
    def test_missing_consume_rejected(self):self.check(fake_log(missing='CALC_CONSUME'),False)
    def test_duplicate_marker_rejected(self):self.check(fake_log(duplicate='MEM_WRITE'),False)
    def test_wrong_xor_rejected(self):self.check(fake_log(after=0x1a),False)
    def test_wrong_drain_byte_rejected(self):self.check(fake_log(word=0x12341878),False)
    def test_delayed_consume_rejected(self):self.check(fake_log(delta_consume=2),False)
    def test_overwrite_rejected(self):self.check(fake_log().replace('overwrites=0','overwrites=1'),False)
    def test_reordered_events_rejected(self):
        s=fake_log();a=s.splitlines();a[4],a[5]=a[5],a[4];self.check('\n'.join(a)+'\n',False)
    def test_wrong_scenario_rejected(self):
        row=dict(ROW,scenario_id='FI_P400_9999');self.check(fake_log(),False,row=row)
    def test_wrong_sample_rejected(self):
        row=dict(ROW,sample_id='IMG0001');self.check(fake_log(),False,row=row)
    def test_failure_marker_rejected(self):
        self.check(fake_log()+'FI_V3B_BUF0007_CONSUMER_NOT_PROVED\n',False)

if __name__=='__main__':unittest.main()
