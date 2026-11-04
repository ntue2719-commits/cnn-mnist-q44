#!/usr/bin/env python3
"""Offline MEM_0003 write/inject/read/capture/MAC-input proof tests."""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path
import unittest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(HERE.parents[1]/'scripts'))
from run_mem1_v3b import (COLUMNS, FROZEN_HASHES, V3B_SNAPSHOT,
    validate_frozen_inputs, parse_consumer, override_prj, TB_PATH_RE)
from pilot400_direct_xsim import classify_elaboration
from pilot400_common import sha256, load_plan, verify_manifest

REPO=HERE.parents[2]
ROW={'scenario_id':'FI_P400_0241','sample_id':'IMG0000',
     'target_id':'MEM_0003','target_class':'MEM','bit_index':'0',
     'injection_offset':'962'}


def fake_log(row=ROW, original=0x12345678, consumes=3, emit_cycles=(1250,1251,1252),
             corrupt_byte=None, overwrite=0, after=None):
    orig=original
    if after is None: after=orig^1
    if corrupt_byte is None: corrupt_byte=after&0xff
    sid=row['scenario_id'];t=row['target_id'];offset=int(row['injection_offset'])
    start=3; inject=offset+start; write=inject-1;read=1134;capture=read+1
    lines=[
      'FI_DISABLED_CONTROL_PASS latency=1342 pool1=676 pool2=200 logits=10 prediction=0',
      f'FI_APPLIED scenario={sid} target={t} class=MEM bit=0 old=0 new=1 requested={inject}',
      f'FI_V3B_MEM1_WRITE scenario={sid} target={t} addr=168 cycle={write} word={orig:08x}',
      f'FI_V3B_MEM1_INJECT scenario={sid} target={t} addr=168 cycle={inject} bit=0 before={orig:08x} after={after:08x}',
      f'FI_V3B_MEM1_READ scenario={sid} target={t} addr=168 cycle={read} word={after:08x}',
      f'FI_V3B_MEM1_CAPTURE scenario={sid} target={t} addr=168 cycle={capture} word={after:08x} byte672={corrupt_byte:02x}',
    ]
    for slot,cycle in enumerate(emit_cycles):
        lines.append(f'FI_V3B_MEM1_WINDOW_EMIT scenario={sid} target={t} slot={slot} cycle={cycle} byte={corrupt_byte:02x}')
        if slot<consumes:
            lines.append(f'FI_V3B_MEM1_CALC_CONSUME scenario={sid} target={t} slot={slot} cycle={cycle+1} byte={corrupt_byte:02x}')
    lines += [
      f'FI_V3B_MEM1_SUMMARY scenario={sid} target={t} write={write} inject={inject} read={read} capture={capture} emits=3 consumes={consumes} overwrites={overwrite} accepted_words=169',
      'FI_RESULT_V2,'+','.join(map(str,[sid,'IMG0000',t,'MEM',0,offset,inject,inject,
          1,1,1345,784,784,784,169,25,1,1,1,1,0,0,0,0,'NONE','FINAL_OUTPUT_MATCH'])),
      'CNN_PEP FI CAMPAIGN V2: PASS (mechanism/log integrity; outcome may be harmful)'
    ]
    return '\n'.join(lines)+'\n'


class Mem1V3bTests(unittest.TestCase):
    def test_frozen_source_and_isolated_tb(self):
        validate_frozen_inputs(REPO)
        for path,expected in FROZEN_HASHES.items():
            self.assertEqual(sha256(REPO/path),expected)
        src=(REPO/'fi/tb/tb_cnn_mnist_fi_pilot400.v').read_bytes()
        probe=(REPO/'fi/v3b/mem1/tb/tb_cnn_mnist_fi_pilot400.v').read_bytes()
        self.assertNotEqual(src,probe)
        self.assertIn(b'FI_V3B_MEM1_CALC_CONSUME',probe)
        self.assertEqual(probe.count(b'module tb_cnn_mnist_fi_pilot400;'),1)

    def test_exact_frozen_scenarios(self):
        plan=load_plan(REPO/'fi/config/pilot400_targets.csv',REPO/'fi/config/fi_targets_candidates_v2.csv')
        rows=verify_manifest(REPO/'fi/scenarios/pilot400.csv',plan)
        chosen=[r for r in rows if r['target_id']=='MEM_0003' and r['sample_id']=='IMG0000']
        self.assertEqual([(r['scenario_id'],r['bit_index'],r['injection_offset']) for r in chosen],
                         [('FI_P400_0241','0','962'),('FI_P400_0242','0','963')])

    def test_single_tb_prj_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            tb=Path(tmp)/'tb_cnn_mnist_fi_pilot400.v';tb.write_text('module fake; endmodule')
            before='verilog xil_defaultlib "C:/Vivado/Simulation.srcs/tb_cnn_mnist_fi_pilot400.v"\nverilog xil_defaultlib "C:/Vivado/Simulation.srcs/feature_mem1.v"\n'
            after=override_prj(before,tb)
            self.assertEqual(after,before.replace('"C:/Vivado/Simulation.srcs/tb_cnn_mnist_fi_pilot400.v"','"'+tb.resolve().as_posix()+'"'))
            self.assertTrue(Path(TB_PATH_RE.findall(after)[0]).samefile(tb))
            with self.assertRaises(ValueError):override_prj(before+before,tb)

    def test_snapshot_name_and_full_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'xsim.dir'/V3B_SNAPSHOT;p.mkdir(parents=True)
            (p/'xsimk.dll').write_bytes(b'kernel')
            header='Running: xelab.exe\nStarting static elaboration\nCompleted static elaboration\n'
            self.assertEqual(classify_elaboration(header+f'Built simulation snapshot {V3B_SNAPSHOT}\n',p,None,expected_snapshot=V3B_SNAPSHOT),'FULL_BUILD')
            with self.assertRaises(ValueError):classify_elaboration(header+'Built simulation snapshot tb_cnn_mnist_fi_pilot400_v3b_behav\n',p,None,expected_snapshot=V3B_SNAPSHOT)

    def check(self,text,ok=True,row=ROW,reference=None):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'test.log';f.write_text(text)
            if reference is not None:
                original=Path(tmp)/'orig.log';original.write_text(reference)
            else: original=None
            if ok:
                rec=parse_consumer(f,row,original)
                self.assertEqual(rec['consumer_proof'],'VERIFIED_CONV2_MAC_INPUT_3_WINDOWS')
                self.assertEqual(rec['outcome'],'FINAL_OUTPUT_MATCH')
                self.assertEqual(set(rec),set(COLUMNS))
                self.assertEqual(rec['calc_consumes'],'3')
            else:
                with self.assertRaises(ValueError):parse_consumer(f,row,original)

    def test_valid_962(self):self.check(fake_log())
    def test_valid_963(self):
        row=ROW.copy();row.update(scenario_id='FI_P400_0242',injection_offset='963')
        self.check(fake_log(row=row),row=row)
    def test_v3a_equivalent_fi_result(self):
        s=fake_log();self.check(s,reference=s)
    def test_v3a_changed_prediction_rejected(self):
        s=fake_log();self.check(s,False,reference=s.replace('NONE,FINAL_OUTPUT_MATCH','FC,WRONG_LOGITS_SAME_LABEL'))
    def test_missing_calc_consumed_rejected(self):self.check(fake_log(consumes=2),False)
    def test_duplicate_emit_marker_rejected(self):
        s=fake_log();line=next(x for x in s.splitlines() if x.startswith('FI_V3B_MEM1_WINDOW_EMIT'))
        self.check(s+line+'\n',False)
    def test_injection_xor_wrong_rejected(self):self.check(fake_log(after=0x1234567b),False)
    def test_source_read_wrong_rejected(self):self.check(fake_log().replace('cycle=1134 word=12345679','cycle=1134 word=12345678'),False)
    def test_capture_byte_wrong_rejected(self):self.check(fake_log(corrupt_byte=0x78),False)
    def test_window_reordered_rejected(self):self.check(fake_log(emit_cycles=(1252,1250,1251)),False)
    def test_calc_not_after_emit_rejected(self):
        s=fake_log().replace('slot=0 cycle=1251 byte=79','slot=0 cycle=1250 byte=79')
        self.check(s,False)
    def test_overwrite_rejected(self):self.check(fake_log(overwrite=1),False)
    def test_accept_count_not_169_rejected(self):self.check(fake_log().replace('accepted_words=169','accepted_words=168'),False)
    def test_wrong_address_rejected(self):self.check(fake_log().replace('addr=168 cycle=1134','addr=167 cycle=1134'),False)
    def test_wrong_sample_rejected(self):
        row=ROW.copy();row['sample_id']='IMG0001';self.check(fake_log(),False,row=row)
    def test_failure_marker_rejected(self):
        self.check(fake_log()+'FI_V3B_MEM1_CONSUMER_NOT_PROVED\n',False)

if __name__=='__main__':unittest.main()
