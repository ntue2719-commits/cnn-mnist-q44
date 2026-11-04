"""Unit tests for V4b-B evidence parser; no Vivado or user evidence required."""
from __future__ import annotations

import unittest

from audit_six_consumers_v4b import (prove_existing_consumer,
                                      PROVED, PENDING, SCHEMA)

GOLDEN = 0x00000ac6
MEM_LOG = """FI_APPLIED scenario=FI_P400_0221 target=MEM_0002 class=MEM bit=0 old=0 new=1 requested=881
FI_TIMELINE_MEM_WRITE cycle=880 value=0a0a0000
FI_TIMELINE_INJECT cycle=881 target=MEM_0002
FI_TIMELINE_MEM_READ cycle=1050 word=0a0a0001
FI_TIMELINE_BUF_CAPTURE cycle=1051 data=0a0a0001
FI_TIMELINE_WINDOW119_EMIT cycle=1256 byte=01
FI_TIMELINE_WINDOW120_EMIT cycle=1257 byte=01
FI_TIMELINE_WINDOW119_CONSUME cycle=1257
FI_TIMELINE_WINDOW120_CONSUME cycle=1258
FI_TIMELINE_MEM_PASS write=880 inject=881 read=1050 capture=1051 emit119=1256 consume119=1257 emit120=1257 consume120=1258
"""
BUF_LOG = """FI_APPLIED scenario=FI_P400_0161 target=BUF_0031 class=BUF bit=0 old=0 new=1 requested=1052
FI_TIMELINE_BUF_CAPTURE cycle=1051 data=0a0a0000
FI_TIMELINE_INJECT cycle=1052 target=BUF_0031
FI_TIMELINE_WINDOW120_EMIT cycle=1257 byte=01
FI_TIMELINE_WINDOW120_CONSUME cycle=1258
FI_TIMELINE_BUF_PASS capture=1051 inject=1052 emit120=1257 consume120=1258
"""


def dat_text(sid: str, bit: int, cycle: int = 1343) -> str:
    old = (GOLDEN >> bit) & 1
    new = 1 - old
    return (f'FI_APPLIED scenario={sid} target=DAT_0010 class=DAT bit={bit} '
            f'old={old} new={new} requested={cycle}\n'
            f'FI_DAT_ARGMAX_CONSUME cycle={cycle} logit0={GOLDEN ^ (1 << bit):08x} '
            f'golden0={GOLDEN:08x} mismatched_classes=1\n')


def row(tid: str, sid: str, bit: int, offset: int) -> dict:
    return {'scenario_id':sid, 'sample_id':'IMG0000', 'target_id':tid,
            'target_class':tid[:3], 'bit_index':str(bit), 'injection_offset':str(offset)}


def parsed(r: dict, cycle: int, outcome: str) -> dict:
    return {'scenario_id':r['scenario_id'], 'target_id':r['target_id'],
            'actual_cycle':str(cycle), 'outcome':outcome,
            'logits_mismatch':'1' if 'WRONG_LOGITS' in outcome else '0',
            'first_bad_stage':'FC' if 'WRONG_LOGITS' in outcome else 'NONE',
            'protocol_ok':'1'}


class ConsumerProofTests(unittest.TestCase):
    def setUp(self):
        self.mem = row('MEM_0002','FI_P400_0221',0,878)
        self.buf = row('BUF_0031','FI_P400_0161',0,1049)
        self.dat0 = row('DAT_0010','FI_P400_0381',0,1340)
        self.dat1 = row('DAT_0010','FI_P400_0382',1,1340)

    def prove_mem(self, txt: str, r: dict | None = None):
        r = r or self.mem
        return prove_existing_consumer(txt,r,parsed(r,881,'FINAL_OUTPUT_MATCH'),GOLDEN)

    def prove_buf(self, txt: str):
        return prove_existing_consumer(txt,self.buf,
                                       parsed(self.buf,1052,'FINAL_OUTPUT_MATCH'),GOLDEN)

    def prove_dat(self, txt: str, r: dict):
        return prove_existing_consumer(txt,r,
                                       parsed(r,1343,'WRONG_LOGITS_SAME_LABEL'),GOLDEN)

    def test_mem_positive(self):
        self.assertEqual(self.prove_mem(MEM_LOG)[0], PROVED['MEM_0002'])

    def test_buf_positive(self):
        self.assertEqual(self.prove_buf(BUF_LOG)[0], PROVED['BUF_0031'])

    def test_dat0_positive(self):
        self.assertEqual(self.prove_dat(dat_text('FI_P400_0381',0),self.dat0)[0],PROVED['DAT_0010'])

    def test_dat1_positive(self):
        self.assertEqual(self.prove_dat(dat_text('FI_P400_0382',1),self.dat1)[0],PROVED['DAT_0010'])

    def test_mem_missing_capture(self):
        with self.assertRaises(ValueError):
            self.prove_mem(MEM_LOG.replace('FI_TIMELINE_BUF_CAPTURE','FI_TIMELINE_OTHER_CAPTURE'))

    def test_mem_wrong_readback(self):
        with self.assertRaises(ValueError):
            self.prove_mem(MEM_LOG.replace('READ cycle=1050 word=0a0a0001',
                                           'READ cycle=1050 word=0a0a0000'))

    def test_mem_wrong_byte_at_mac(self):
        with self.assertRaises(ValueError):
            self.prove_mem(MEM_LOG.replace('WINDOW120_EMIT cycle=1257 byte=01',
                                           'WINDOW120_EMIT cycle=1257 byte=00'))

    def test_mem_wrong_consume_cycle(self):
        with self.assertRaises(ValueError):
            self.prove_mem(MEM_LOG.replace('WINDOW120_CONSUME cycle=1258',
                                           'WINDOW120_CONSUME cycle=1259'))

    def test_mem_tampered_summary(self):
        with self.assertRaises(ValueError):
            self.prove_mem(MEM_LOG.replace('consume120=1258','consume120=1260'))

    def test_mem_duplicate_marker(self):
        with self.assertRaises(ValueError):
            self.prove_mem(MEM_LOG + 'FI_TIMELINE_MEM_READ cycle=1050 word=0a0a0001\n')

    def test_mem_explicit_failure_marker(self):
        with self.assertRaises(ValueError):
            self.prove_mem(MEM_LOG + 'FI_TIMELINE_FAIL: monitor failed\n')

    def test_buf_missing_emit(self):
        with self.assertRaises(ValueError):
            self.prove_buf(BUF_LOG.replace('FI_TIMELINE_WINDOW120_EMIT','FI_TIMELINE_WINDOW120_OTHER'))

    def test_buf_wrong_byte(self):
        with self.assertRaises(ValueError):
            self.prove_buf(BUF_LOG.replace('WINDOW120_EMIT cycle=1257 byte=01',
                                           'WINDOW120_EMIT cycle=1257 byte=02'))

    def test_buf_wrong_timeline_order(self):
        with self.assertRaises(ValueError):
            self.prove_buf(BUF_LOG.replace('INJECT cycle=1052','INJECT cycle=1257'))

    def test_buf_invalid_xor_readback(self):
        with self.assertRaises(ValueError):
            self.prove_buf(BUF_LOG.replace('old=0 new=1','old=1 new=0'))

    def test_dat_missing_consumer(self):
        with self.assertRaises(ValueError):
            self.prove_dat(dat_text('FI_P400_0381',0).replace('FI_DAT_ARGMAX_CONSUME',
                                                           'FI_ARGMAX_OTHER_CONSUME'),self.dat0)

    def test_dat_wrong_golden(self):
        with self.assertRaises(ValueError):
            self.prove_dat(dat_text('FI_P400_0381',0).replace('golden0=00000ac6',
                                                           'golden0=00000ac7'),self.dat0)

    def test_dat_wrong_consumed_value(self):
        with self.assertRaises(ValueError):
            self.prove_dat(dat_text('FI_P400_0381',0).replace('logit0=00000ac7',
                                                           'logit0=00000ac6'),self.dat0)

    def test_dat_wrong_consumer_mismatch_class_count(self):
        with self.assertRaises(ValueError):
            self.prove_dat(dat_text('FI_P400_0381',0).replace('mismatched_classes=1',
                                                           'mismatched_classes=0'),self.dat0)

    def test_dat_duplicate_consume(self):
        txt=dat_text('FI_P400_0381',0)
        with self.assertRaises(ValueError):
            self.prove_dat(txt + txt.splitlines()[1] + '\n',self.dat0)

    def test_dat_wrong_bit_pair(self):
        txt=dat_text('FI_P400_0382',1)
        with self.assertRaises(ValueError):
            self.prove_dat(txt,self.dat0)

    def test_dat_wrong_time_order(self):
        with self.assertRaises(ValueError):
            self.prove_dat(dat_text('FI_P400_0381',0,1342).replace('requested=1342',
                                                                  'requested=1343'),self.dat0)

    def test_dat_wrong_bit_old(self):
        with self.assertRaises(ValueError):
            self.prove_dat(dat_text('FI_P400_0381',0).replace('old=0 new=1',
                                                           'old=1 new=0'),self.dat0)

    def test_pending_not_upgraded(self):
        for tid in PENDING:
            r=row(tid,'FI_P400_0001',0,100)
            status,marker = prove_existing_consumer('',r,
                                           parsed(r,103,'FINAL_OUTPUT_MATCH'),GOLDEN)
            self.assertEqual(status,'NOT_PROVED_IN_V4B_A_LOGS')
            self.assertEqual(marker,'NONE')

    def test_closed_set(self):
        self.assertEqual(len(PROVED),3)
        self.assertEqual(len(PENDING),3)
        self.assertEqual(len(SCHEMA)>10,True)


if __name__=='__main__':
    unittest.main()
