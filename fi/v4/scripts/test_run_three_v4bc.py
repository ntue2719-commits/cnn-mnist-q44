#!/usr/bin/env python3
"""Offline adversarial tests for V4b-C isolated control/data proof parser."""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from run_three_v4bc import (
    EXPECTED,TARGETS,TB_REL,SNAPSHOT,PROBE_TB_SHA256,COLUMNS,
    validate_frozen_inputs,selected_three,parse_consumer,
    SUMMARY_RE,CTL_RE,EV_RE,validate_full_timing_gate,
)
from pilot400_common import load_plan,verify_manifest,sha256
from run_mem1_v3b import override_prj
from pilot400_direct_xsim import classify_elaboration

ROOT=Path(__file__).resolve().parents[3]


def frozen_row(sid:str)->dict[str,str]:
    tid,bit,offset,_=EXPECTED[sid]
    return dict(scenario_id=sid,sample_id='IMG0000',target_id=tid,
                target_class=tid.split('_')[0],bit_index=bit,injection_offset=offset)


def fixture(row:dict[str,str], old_byte:int=0x54)->str:
    sid=row['scenario_id'];tid=row['target_id']
    cyc=int(row['injection_offset'])+3
    oldbit=(old_byte&1) if tid!='CTL_0001' else 0
    newbit=1-oldbit
    applied=f'FI_APPLIED scenario={sid} target={tid} class={row["target_class"]} bit={row["bit_index"]} old={oldbit} new={newbit} requested={cyc}'
    lines=['FI_DISABLED_CONTROL_PASS latency=1342 pool1=676 pool2=200 logits=10 prediction=0']
    if tid=='CTL_0001':
        lines.append(applied)
        lines.append(f'FI_V4BC_CTL scenario={sid} target={tid} cycle={cyc} bit=2 old=1 new=5 post=0 valid=1 done=1 busy=0')
        lines.append(f'FI_V4BC_SUMMARY scenario={sid} target={tid} inject={cyc} decision={cyc} write=-1 read=-1 capture=-1 window=-1 consume=-1 overwrites=0')
    elif tid=='BUF_0030':
        cap=cyc-4;emit=1255;cons=emit+1
        if cyc>=emit: emit=cyc+100;cons=emit+1
        lines.extend([
          f'FI_V4BC_BUF_CAPTURE scenario={sid} target={tid} cycle={cap} word=000000{old_byte:02x}',
          applied,
          f'FI_V4BC_INJECT scenario={sid} target={tid} cycle={cyc} bit=0 old={old_byte:02x} new={old_byte^1:02x}',
          f'FI_V4BC_WINDOW scenario={sid} target={tid} cycle={emit} slot=0 byte={old_byte^1:02x}',
          f'FI_V4BC_MAC scenario={sid} target={tid} cycle={cons} slot=0 byte={old_byte^1:02x}',
          f'FI_V4BC_SUMMARY scenario={sid} target={tid} inject={cyc} decision=-1 write=-1 read=-1 capture={cap} window={emit} consume={cons} overwrites=0',
        ])
    else:
        write=cyc-5;read=cyc+100;cap=read+1;emit=1255;cons=emit+1
        if cyc>=emit:emit=cyc+200;cons=emit+1
        orig=0x12003400|old_byte
        change=orig^1
        lines.extend([
          f'FI_V4BC_MEM_WRITE scenario={sid} target={tid} cycle={write} word={orig:08x}',
          applied,
          f'FI_V4BC_INJECT scenario={sid} target={tid} cycle={cyc} bit=0 old={old_byte:02x} new={old_byte^1:02x}',
          f'FI_V4BC_MEM_READ scenario={sid} target={tid} cycle={read} word={change:08x}',
          f'FI_V4BC_BUF_CAPTURE scenario={sid} target={tid} cycle={cap} word={change:08x}',
          f'FI_V4BC_WINDOW scenario={sid} target={tid} cycle={emit} slot=0 byte={old_byte^1:02x}',
          f'FI_V4BC_MAC scenario={sid} target={tid} cycle={cons} slot=0 byte={old_byte^1:02x}',
          f'FI_V4BC_SUMMARY scenario={sid} target={tid} inject={cyc} decision=-1 write={write} read={read} capture={cap} window={emit} consume={cons} overwrites=0',
        ])
    outcome=EXPECTED[sid][3]
    protocol=0 if outcome=='PROTOCOL_VIOLATION' else 1
    # FI_RESULT_V2 strict schema includes 26 fields; same golden values as V4b-A.
    result=[sid,'IMG0000',tid,row['target_class'],row['bit_index'],row['injection_offset'],cyc,cyc,
            1,1,180 if protocol==0 else 1345,120 if protocol==0 else 784,
            784,784,169,25,1,1,1,protocol,0,0,0,0,
            'PROTOCOL' if protocol==0 else 'NONE',outcome]
    lines.append('FI_RESULT_V2,'+','.join(map(str,result)))
    lines.append('CNN_PEP FI CAMPAIGN V2: PASS (mechanism/log integrity; outcome may be harmful)')
    return '\n'.join(lines)+'\n'


class FrozenSourceContracts(unittest.TestCase):
    def test_frozen_original_and_isolated_monitor(self):
        validate_frozen_inputs(ROOT)
        self.assertEqual(sha256(ROOT/TB_REL),PROBE_TB_SHA256)
        self.assertNotEqual(sha256(ROOT/TB_REL),sha256(ROOT/'fi/tb/tb_cnn_mnist_fi_pilot400.v'))

    def test_exact_six_scenarios(self):
        plan=load_plan(ROOT/'fi/config/pilot400_targets.csv',
                       ROOT/'fi/config/fi_targets_candidates_v2.csv')
        rows=selected_three(verify_manifest(ROOT/'fi/scenarios/pilot400.csv',plan))
        self.assertEqual([r['scenario_id'] for r in rows],list(EXPECTED))
        self.assertEqual({r['target_id'] for r in rows},set(TARGETS))
        with self.assertRaises(ValueError):selected_three(rows+[rows[0]])

    def test_only_temporary_testbench_swapped(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'tb_cnn_mnist_fi_pilot400.v';p.write_text('module fake;endmodule')
            prj='verilog xil_defaultlib "C:/RTL/conv2_buf.v"\nverilog xil_defaultlib "C:/old/tb_cnn_mnist_fi_pilot400.v"\n'
            dest=override_prj(prj,p)
            self.assertIn(p.resolve().as_posix(),dest)
            self.assertIn('C:/RTL/conv2_buf.v',dest)
            self.assertNotIn('"C:/old/tb_cnn_mnist_fi_pilot400.v"',dest)
            with self.assertRaises(ValueError):override_prj(prj+prj,p)

    def test_separate_snapshot_marker(self):
        with tempfile.TemporaryDirectory() as d:
            snap=Path(d)/'xsim.dir'/SNAPSHOT;snap.mkdir(parents=True)
            (snap/'xsimk.dll').write_bytes(b'kernel')
            s='Running: C:/Vivado/bin/xelab.exe --snapshot '+SNAPSHOT+'\nStarting static elaboration\nCompleted static elaboration\nBuilt simulation snapshot '+SNAPSHOT+'\n'
            self.assertEqual(classify_elaboration(s,snap,None,expected_snapshot=SNAPSHOT),'FULL_BUILD')
            with self.assertRaises(ValueError):classify_elaboration(s.replace(SNAPSHOT,'wrong_snapshot'),snap,None,expected_snapshot=SNAPSHOT)

    def test_exact_event_contract_patterns(self):
        self.assertEqual(len(EV_RE),6)
        self.assertTrue(SUMMARY_RE.match('FI_V4BC_SUMMARY scenario=A target=B inject=2 decision=-1 write=-1 read=-1 capture=1 window=3 consume=4 overwrites=0'))
        self.assertTrue(CTL_RE.match('FI_V4BC_CTL scenario=A target=B cycle=3 bit=2 old=1 new=5 post=0 valid=1 done=1 busy=0'))


class LogProofTests(unittest.TestCase):
    def check(self,text:str,row:dict[str,str],pass_expected:bool=True,reference:str|None=None):
        with tempfile.TemporaryDirectory() as d:
            log=Path(d)/'current.log';log.write_text(text,encoding='utf-8')
            parent=None
            if reference is not None:
                parent=Path(d)/'v4ba.log';parent.write_text(reference,encoding='utf-8')
            if pass_expected:
                verified=parse_consumer(log,row,parent)
                self.assertEqual(set(verified),set(COLUMNS))
                self.assertEqual(verified['outcome'],EXPECTED[row['scenario_id']][3])
                self.assertEqual(verified['consumer_kind'],TARGETS[row['target_id']])
            else:
                with self.assertRaises(ValueError):parse_consumer(log,row,parent)

    def test_good_six_with_reference(self):
        for sid in EXPECTED:
            with self.subTest(scenario=sid):
                r=frozen_row(sid);f=fixture(r)
                self.check(f,r,True,f)

    def test_wrong_parent_result(self):
        for sid in EXPECTED:
            r=frozen_row(sid);f=fixture(r)
            self.check(f,r,False,f.replace('IMG0000','IMG0001'))

    def test_wrong_bit_and_identity(self):
        for sid in EXPECTED:
            r=frozen_row(sid);f=fixture(r)
            self.check(f.replace(f'target={r["target_id"]} ', 'target=DAT_9999 ',1),r,False)
            self.check(f.replace('requested='+str(int(r['injection_offset'])+3),
                                 'requested='+str(int(r['injection_offset'])+4),1),r,False)
            self.check(f.replace(' old=0 new=1 ', ' old=0 new=0 ',1),r,False)

    def test_missing_summary(self):
        for sid in EXPECTED:
            r=frozen_row(sid)
            lines=[s for s in fixture(r).splitlines() if not s.startswith('FI_V4BC_SUMMARY ')]
            self.check('\n'.join(lines)+'\n',r,False)

    def test_duplicate_strict_event(self):
        for sid in EXPECTED:
            r=frozen_row(sid);f=fixture(r)
            event=next(l for l in f.splitlines() if l.startswith('FI_V4BC_') and not l.startswith('FI_V4BC_SUMMARY'))
            self.check(f+event+'\n',r,False)

    def test_unknown_marker_is_rejected(self):
        for sid in EXPECTED:
            r=frozen_row(sid);self.check(fixture(r)+'FI_V4BC_UNRECOGNIZED hello\n',r,False)

    def test_missing_mac_proof(self):
        for sid in EXPECTED:
            if EXPECTED[sid][0]=='CTL_0001':continue
            r=frozen_row(sid)
            f='\n'.join(l for l in fixture(r).splitlines() if not l.startswith('FI_V4BC_MAC '))+'\n'
            self.check(f,r,False)

    def test_mac_wrong_byte_or_slot(self):
        for sid in EXPECTED:
            if EXPECTED[sid][0]=='CTL_0001':continue
            r=frozen_row(sid);f=fixture(r)
            self.check(re.sub(r'(FI_V4BC_MAC[^\n]+)slot=0',r'\g<1>slot=1',f),r,False)
            self.check(re.sub(r'(FI_V4BC_MAC[^\n]+)byte=55',r'\g<1>byte=54',f),r,False)

    def test_wrong_chronology(self):
        for sid in EXPECTED:
            if EXPECTED[sid][0]=='CTL_0001':continue
            r=frozen_row(sid);f=fixture(r)
            match=re.search(r'(FI_V4BC_MAC[^\n]+)cycle=(\d+)',f)
            self.assertTrue(match)
            self.check(f.replace('FI_V4BC_MAC '+match.group(0).split('FI_V4BC_MAC ')[-1],
                                 'FI_V4BC_MAC '+match.group(0).split('FI_V4BC_MAC ')[-1].replace('cycle='+match.group(2),'cycle=9999')),r,False)

    def test_mem1_wrong_read_word(self):
        for sid in EXPECTED:
            if EXPECTED[sid][0]!='MEM_0001':continue
            r=frozen_row(sid);f=fixture(r)
            self.check(f.replace('word=12003455','word=12003454'),r,False)
            self.check(f.replace('word=12003455','word=12003457'),r,False)

    def test_buf0030_bad_capture(self):
        for sid in EXPECTED:
            if EXPECTED[sid][0]!='BUF_0030':continue
            r=frozen_row(sid);f=fixture(r)
            self.check(f.replace('word=00000054','word=00000056'),r,False)

    def test_ctl_wrong_branch(self):
        for sid in EXPECTED:
            if EXPECTED[sid][0]!='CTL_0001':continue
            r=frozen_row(sid);f=fixture(r)
            for part in ('new=4','post=5','valid=0','done=0','busy=1'):
                good={'new=4':'new=5','post=5':'post=0','valid=0':'valid=1','done=0':'done=1','busy=1':'busy=0'}[part]
                self.check(f.replace(good,part),r,False)

    def test_ctl_proves_protocol_effect_not_same_label(self):
        sid='FI_P400_0001';r=frozen_row(sid);f=fixture(r)
        self.assertIn('PROTOCOL_VIOLATION',f)
        self.check(f.replace('PROTOCOL,PROTOCOL_VIOLATION','NONE,FINAL_OUTPUT_MATCH'),r,False)

    def test_no_fake_pass_marker(self):
        for sid in EXPECTED:
            r=frozen_row(sid);f=fixture(r)
            self.check(f+'ERROR: [XLAB] fail\n',r,False)
            self.check(f.replace('CNN_PEP FI CAMPAIGN V2: PASS','CNN_PEP FI CAMPAIGN V2: UNVERIFIED'),r,False)

    def test_unexpected_outcome(self):
        r=frozen_row('FI_P400_0201');f=fixture(r)
        self.check(f.replace('NONE,FINAL_OUTPUT_MATCH','FC,WRONG_LOGITS_SAME_LABEL'),r,False)

    def test_selected_status_not_modified(self):
        self.assertEqual(tuple(EXPECTED),('FI_P400_0001','FI_P400_0002','FI_P400_0141','FI_P400_0142','FI_P400_0201','FI_P400_0202'))


if __name__=='__main__':unittest.main()
