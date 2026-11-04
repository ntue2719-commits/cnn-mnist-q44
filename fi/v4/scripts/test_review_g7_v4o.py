import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
import review_g7_v4o as m

ROOT=Path(os.environ.get('CNN_PEP_G7_EVIDENCE_ROOT', '.')).resolve()
HANDOFF=ROOT/'CNN_PEP_AI_READY_FULL_HANDOFF_2026-11-04.zip'
M=ROOT/'cnn_pep_v4m_g7l2_img0000_results_01.zip'
N=ROOT/'cnn_pep_v4n_g7l2_historical_results_01.zip'


def mutate_zip(inp, out, name, change, summand=None):
    """Rewrites only a temp copy; optionally recomputes target SHA256SUMS."""
    with zipfile.ZipFile(inp) as src:
        entries={k:src.read(k) for k in src.namelist()}
    entries[name]=change(entries[name])
    if summand:
        sums=summand+'SHA256SUMS.txt'
        lines=[]
        for line in entries[sums].decode().splitlines():
            h,filename=line.split('  ',1)
            if filename==name[len(summand):]:
                h=m.sha(entries[name])
            lines.append(h+'  '+filename)
        entries[sums]=('\n'.join(lines)+'\n').encode()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as dst:
        for k,v in entries.items(): dst.writestr(k,v)

class ReviewTests(unittest.TestCase):
    def test_full_read_only_evidence(self):
        got=m.review(HANDOFF,M,N)
        self.assertEqual((got['raw_logs_independently_rehashed'],got['golden_pool1_bytes_recomputed']), (40,676))
        self.assertEqual(got['same_scenario_increment']['L2_over_L1'],4)
        self.assertFalse(got['full400_authorized'])

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(m.Refused):
            m.json_obj(b'{"g7":false,"g7":true}')

    def test_bad_l2_negative_not_match(self):
        self.assertEqual(m.stage(0,0,169,4),'NOT_OBSERVED')
        self.assertEqual(m.stage(5,0,169,4),'INCONCLUSIVE')
        self.assertEqual(m.stage(169,0,169,4),'MATCH')
        with self.assertRaises(m.Refused): m.stage(170,0,169,4)

    def test_l1_positive_inherits_l2_when_stage_missing(self):
        self.assertEqual(m.levels('PROTOCOL_VIOLATION','NOT_OBSERVED','NOT_OBSERVED'),('TRUE','TRUE','TRUE'))

    def test_final_match_needs_complete_stage(self):
        self.assertEqual(m.levels('FINAL_OUTPUT_MATCH','MATCH','NOT_OBSERVED')[-1],'INCONCLUSIVE')
        self.assertEqual(m.levels('FINAL_OUTPUT_MATCH','MATCH','MISMATCH')[-1],'TRUE')
        self.assertEqual(m.levels('FINAL_OUTPUT_MATCH','MATCH','MATCH')[-1],'FALSE')

    def test_misclassification_vs_wrong_logits(self):
        self.assertEqual(m.levels('WRONG_LOGITS_SAME_LABEL','MATCH','MATCH'),('FALSE','TRUE','TRUE'))

    def test_corrupt_summary_rejected_even_with_rehashed_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bad.zip'
            target='v4m_g7_l2_img0000_01/v4m_g7_l2_summary.json'
            def change(b):
                d=json.loads(b)
                d['full400_authorized']=True
                return (json.dumps(d,indent=2)+'\n').encode()
            mutate_zip(M,path,target,change,'v4m_g7_l2_img0000_01/')
            with self.assertRaises(m.Refused): m.review(HANDOFF,path,N)

    def test_corrupt_csv_counter_rejected_even_with_rehashed_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bad.zip'
            target='v4m_g7_l2_img0000_01/v4m_g7_l2_40.csv'
            def change(b):
                return b.replace(b'FI_P400_0101,IMG0000,BUF_0007,BUF,0,791,FINAL_OUTPUT_MATCH,POOL1,169,1,',
                                 b'FI_P400_0101,IMG0000,BUF_0007,BUF,0,791,FINAL_OUTPUT_MATCH,POOL1,169,2,',1)
            mutate_zip(M,path,target,change,'v4m_g7_l2_img0000_01/')
            with self.assertRaises(m.Refused): m.review(HANDOFF,path,N)

    def test_corrupt_witness_rejected_even_with_rehashed_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bad.zip'
            target='v4n_historical_witness_01/v4n_four_l2_witnesses.csv'
            def change(b):
                return b.replace(b'FI_P400_0101,BUF_0007,BUF_REORDER_VALUE_CHAIN,337,0,1',
                                 b'FI_P400_0101,BUF_0007,BUF_REORDER_VALUE_CHAIN,337,0,2',1)
            mutate_zip(N,path,target,change,'v4n_historical_witness_01/')
            with self.assertRaises(m.Refused): m.review(HANDOFF,M,path)

    def test_duplicate_zip_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'duplicate.zip'
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter('ignore',UserWarning)
                with zipfile.ZipFile(path,'w') as z:
                    z.writestr('p.txt',b'x')
                    z.writestr('p.txt',b'y')
            with self.assertRaises(m.Refused): m.safe_zip(path)

    def test_independent_pool1_2x2_arithmetic(self):
        a=[0]*2704
        self.assertEqual(len(m.pool1(a)),676)
        a[0]=7
        self.assertEqual(m.pool1(a)[0],7)

if __name__=='__main__':unittest.main()
