#!/usr/bin/env python3
"""Offline V3b proof gate tests; these do not execute Vivado or XSim."""
from __future__ import annotations
from pathlib import Path
import sys
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(HERE.parents[1]/'scripts'))
from run_mem2_v3b import (FROZEN_HASHES, TARGETS, COLUMNS, TB_PATH_RE,
                          override_prj,parse_consumer,validate_frozen_inputs,
                          V3B_SNAPSHOT)
from pilot400_direct_xsim import classify_elaboration
from pilot400_common import sha256

REPO=HERE.parents[2]


def fake_log(sid='FI_P400_0261',target='MEM_0004',bit=0,offset=1292,
             read=1317,consume=1318, original=0xAABBCCDD88FFEEDD,
             data=None, overwrite=0, duplicate=False):
    if data is None:data=original ^ (1 << bit)
    after=original^(1<<bit)
    start=3
    applied_cycle=offset+start
    addr=TARGETS[target]
    payload=(
        'FI_DISABLED_CONTROL_PASS latency=1342 pool1=676 pool2=200 logits=10 prediction=0\n'
        f'FI_APPLIED scenario={sid} target={target} class=MEM bit={bit} old=1 new=0 requested={applied_cycle}\n'
        f'FI_V3B_MEM2_READ_PASS scenario={sid} target={target} addr={addr} cycle={read} word={after:016x}\n'
        f'FI_V3B_MEM2_CONSUMER_PASS scenario={sid} target={target} addr={addr} bit={bit} read={read} consume={consume} before={original:016x} after={after:016x} data={data:016x}\n'
        f'FI_V3B_MEM2_SUMMARY scenario={sid} target={target} read_cycle={read} consume_cycle={consume} proofs=1 overwrite_edges={overwrite} fc_words=25\n'
        'FI_RESULT_V2,'+','.join(map(str,[sid,'IMG0000',target,'MEM',bit,offset,applied_cycle,applied_cycle,
          1,1,1345,784,784,784,169,25,1,1,1,1,0,0,1,0,'FC','WRONG_LOGITS_SAME_LABEL']))+'\n'
        'CNN_PEP FI CAMPAIGN V2: PASS (mechanism/log integrity; outcome may be harmful)\n'
    )
    if duplicate:payload=payload.replace('FI_V3B_MEM2_CONSUMER_PASS', 'FI_V3B_MEM2_CONSUMER_PASS',1)+\
          next(line for line in payload.splitlines(keepends=True) if line.startswith('FI_V3B_MEM2_CONSUMER_PASS'))
    return payload


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.row={'scenario_id':'FI_P400_0261','sample_id':'IMG0000','target_id':'MEM_0004',
                  'target_class':'MEM','bit_index':'0','injection_offset':'1292'}

    def _check(self,text,success=True):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'run.log';p.write_text(text)
            if success:
                r=parse_consumer(p,self.row)
                self.assertEqual(r['consumer_proof'],'VERIFIED_FC_INPUT')
                self.assertEqual(r['outcome'],'WRONG_LOGITS_SAME_LABEL')
                self.assertEqual(set(r),set(COLUMNS))
            else:
                with self.assertRaises(ValueError):parse_consumer(p,self.row)

    def test_frozen_sha256_original_tb_exact(self):
        validate_frozen_inputs(REPO)
        self.assertEqual(sha256(REPO/'fi/tb/tb_cnn_mnist_fi_pilot400.v'),
                         FROZEN_HASHES['fi/tb/tb_cnn_mnist_fi_pilot400.v'])

    def test_one_source_replacement_only(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'tb_cnn_mnist_fi_pilot400.v';p.write_text('module tb; endmodule')
            src='verilog xil_defaultlib "C:/Sim/tb_cnn_mnist_fi_pilot400.v"\nverilog xil_defaultlib "C:/Sim/rtl.v"\n'
            dst=override_prj(src,p)
            # On Windows, tempfile can return an 8.3 path (TRITUE~1),
            # while Path.resolve() expands it to a long path (Tri Tue).
            # Compare against the same canonical path the runner actually emits,
            # then verify that the quoted replacement resolves to the same file.
            expected=src.replace('"C:/Sim/tb_cnn_mnist_fi_pilot400.v"',
                                 '"'+p.resolve().as_posix()+'"')
            self.assertEqual(dst,expected)  # all other HDL lines unchanged
            references=TB_PATH_RE.findall(dst)
            self.assertEqual(len(references),1)
            self.assertTrue(Path(references[0]).samefile(p))
            self.assertIn('"C:/Sim/rtl.v"',dst)
            self.assertNotIn('"C:/Sim/tb_cnn_mnist_fi_pilot400.v"',dst)
            with self.assertRaises(ValueError):override_prj('verilog xil_defaultlib "C:/Sim/rtl.v"\n',p)
            with self.assertRaises(ValueError):override_prj(src+src,p)

    def test_one_source_replacement_with_spaces_in_path(self):
        with tempfile.TemporaryDirectory() as folder:
            special=Path(folder)/'directory with spaces'
            special.mkdir()
            tb=special/'tb_cnn_mnist_fi_pilot400.v'
            tb.write_text('module tb; endmodule')
            src=('verilog xil_defaultlib "C:/Simulation/tb_cnn_mnist_fi_pilot400.v"\n'
                 'verilog xil_defaultlib "C:/Simulation/conv1_layer.v"\n')
            dst=override_prj(src,tb)
            self.assertIn('"'+tb.resolve().as_posix()+'"',dst)
            self.assertTrue(Path(TB_PATH_RE.findall(dst)[0]).samefile(tb))
            self.assertIn('"C:/Simulation/conv1_layer.v"',dst)

    def test_v3b_new_snapshot_build_marker_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            snapshot = Path(folder)/'xsim.dir'/V3B_SNAPSHOT
            snapshot.mkdir(parents=True)
            (snapshot/'xsimk.dll').write_bytes(b'new isolated kernel')
            log = ('Running: C:/Vivado/xelab.exe --snapshot '+V3B_SNAPSHOT+'\n'
                   'Starting static elaboration\nCompleted static elaboration\n'
                   'Built simulation snapshot '+V3B_SNAPSHOT+'\n')
            self.assertEqual(classify_elaboration(
                log, snapshot, None, expected_snapshot=V3B_SNAPSHOT), 'FULL_BUILD')

    def test_v3b_rejects_other_snapshot_even_if_xelab_succeeded(self):
        with tempfile.TemporaryDirectory() as folder:
            snapshot = Path(folder)/'xsim.dir'/V3B_SNAPSHOT
            snapshot.mkdir(parents=True)
            (snapshot/'xsimk.dll').write_bytes(b'kernel')
            prefix = ('Running: C:/Vivado/xelab.exe\nStarting static elaboration\n'
                      'Completed static elaboration\n')
            for output in ('Built simulation snapshot tb_cnn_mnist_fi_pilot400_behav',
                           'Built simulation snapshot '+V3B_SNAPSHOT+'_mismatched',
                           'No build snapshot marker'):
                with self.subTest(output=output):
                    with self.assertRaisesRegex(ValueError, 'neither'):
                        classify_elaboration(prefix+output, snapshot, None,
                                             expected_snapshot=V3B_SNAPSHOT)

    def test_v3b_fails_on_snapshot_directory_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            snapshot = Path(folder)/'xsim.dir'/'wrong_snapshot'
            snapshot.mkdir(parents=True)
            (snapshot/'xsimk.dll').write_bytes(b'kernel')
            log = ('Running: xelab\nStarting static elaboration\n'
                   'Completed static elaboration\n'
                   'Built simulation snapshot '+V3B_SNAPSHOT+'\n')
            with self.assertRaisesRegex(ValueError, 'directory does not match'):
                classify_elaboration(log, snapshot, None, expected_snapshot=V3B_SNAPSHOT)

    def test_v3b_build_marker_requires_real_snapshot_files(self):
        with tempfile.TemporaryDirectory() as folder:
            snapshot = Path(folder)/'xsim.dir'/V3B_SNAPSHOT
            snapshot.mkdir(parents=True)
            log = ('Running: xelab\nStarting static elaboration\n'
                   'Completed static elaboration\n'
                   'Built simulation snapshot '+V3B_SNAPSHOT+'\n')
            with self.assertRaisesRegex(ValueError, 'missing/empty'):
                classify_elaboration(log, snapshot, None, expected_snapshot=V3B_SNAPSHOT)

    def test_valid_fc_consumer_proof(self):self._check(fake_log())
    def test_wrong_consumer_data_rejected(self):self._check(fake_log(data=0xAABBCCDD88FFEEDD),False)
    def test_overwritten_before_read_rejected(self):self._check(fake_log(overwrite=1),False)
    def test_duplicate_proofs_rejected(self):self._check(fake_log(duplicate=True),False)
    def test_read_before_injection_rejected(self):self._check(fake_log(read=1293,consume=1318),False)
    def test_consume_before_read_rejected(self):self._check(fake_log(read=1318,consume=1317),False)
    def test_changed_addr_rejected(self):
        self._check(fake_log().replace('target=MEM_0004 addr=0 bit=', 'target=MEM_0004 addr=12 bit='),False)
    def test_missing_fc_valid_proof_rejected(self):
        self._check(fake_log().replace('FI_V3B_MEM2_CONSUMER_PASS','FI_V3B_MEM2_CONSUMER_NOT_PROVED'),False)
    def test_mem0005_proof(self):
        row=self.row.copy()
        row.update(scenario_id='FI_P400_0281',target_id='MEM_0005',injection_offset='1304')
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'run.log';p.write_text(fake_log(sid='FI_P400_0281',target='MEM_0005',offset=1304,read=1329,consume=1330))
            r=parse_consumer(p,row)
            self.assertEqual(r['target_id'],'MEM_0005')

if __name__=='__main__':unittest.main()
