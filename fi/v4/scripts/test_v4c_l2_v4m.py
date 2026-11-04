import io
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stderr

import analyze_v4c_l2_v4m as m
import audit_v4c_l2_v4m as auditor

FROZEN={'scenario_id':'FI_P400_0101','sample_id':'IMG0000','target_id':'BUF_0007',
        'target_class':'BUF','bit_index':'0','injection_offset':'791'}


def valid_log(*,p1w=169,p1m=1,p2w=25,p2m=0,logits=0,outcome='FINAL_OUTPUT_MATCH',pred=0):
    # FI_RESULT_V2 26 data fields + marker (indices 0..26)
    f=['FI_RESULT_V2','FI_P400_0101','IMG0000','BUF_0007','BUF',
       '0','791','794','794','1','1','1345','784','784','784',
       str(p1w),str(p2w),'1','1','1','1',str(p1m),str(p2m),str(logits),str(pred),
       'POOL1' if p1m else ('POOL2' if p2m else ('FC' if logits else 'NONE')),outcome]
    assert len(f)==27
    return ('FI_APPLIED scenario=FI_P400_0101 target=BUF_0007 class=BUF bit=0 old=0 new=1 requested=794\n'
            +'OUTCOME='+outcome+' FIRST_BAD_STAGE='+f[25]+'\n'
            +','.join(f)+'\n').encode('utf8')


class StageContract(unittest.TestCase):
    def test_full_match(self): self.assertEqual(m.stage(169,0,169,4),'MATCH')
    def test_full_mismatch(self): self.assertEqual(m.stage(169,1,169,4),'MISMATCH')
    def test_partial_mismatch(self): self.assertEqual(m.stage(1,1,169,4),'MISMATCH')
    def test_partial_negative_inconclusive(self): self.assertEqual(m.stage(168,0,169,4),'INCONCLUSIVE')
    def test_not_observed(self): self.assertEqual(m.stage(0,0,169,4),'NOT_OBSERVED')
    def test_zero_words_nonzero_mismatch_refused(self):
        with self.assertRaises(ValueError): m.stage(0,1,169,4)
    def test_too_many_words_refused(self):
        with self.assertRaises(ValueError): m.stage(170,0,169,4)
    def test_too_many_mismatches_refused(self):
        with self.assertRaises(ValueError): m.stage(1,5,169,4)
    def test_negative_words_refused(self):
        with self.assertRaises(ValueError): m.stage(-1,0,169,4)
    def test_float_not_int_refused(self):
        with self.assertRaises(ValueError): m.stage(169.0,0,169,4)
    def test_l2_inherits_l1(self): self.assertEqual(m.levels('WRONG_LOGITS_SAME_LABEL','NOT_OBSERVED','NOT_OBSERVED'),('FALSE','TRUE','TRUE'))
    def test_l0_harmful(self): self.assertEqual(m.levels('WRONG_LABEL','MATCH','MATCH'),('TRUE','TRUE','TRUE'))
    def test_l2_only_mismatch(self): self.assertEqual(m.levels('FINAL_OUTPUT_MATCH','MISMATCH','MATCH'),('FALSE','FALSE','TRUE'))
    def test_l2_false_complete(self): self.assertEqual(m.levels('FINAL_OUTPUT_MATCH','MATCH','MATCH'),('FALSE','FALSE','FALSE'))
    def test_l2_inconclusive_missing(self): self.assertEqual(m.levels('FINAL_OUTPUT_MATCH','MATCH','NOT_OBSERVED'),('FALSE','FALSE','INCONCLUSIVE'))
    def test_unrecognized_outcome(self):
        with self.assertRaises(ValueError): m.levels('TIMEOUT_INFRA','MATCH','MATCH')


class RawParser(unittest.TestCase):
    def parse(self,b=None): return m.parse_log(valid_log() if b is None else b,FROZEN,0)
    def test_valid_l2_only(self):
        r=self.parse()
        self.assertEqual((r['l0'],r['l1'],r['l2']),('FALSE','FALSE','TRUE'))
        self.assertEqual((r['pool1_words'],r['pool1_mismatch']), (169,1))
    def test_complete_no_mismatch_l2_false(self):
        r=self.parse(valid_log(p1m=0))
        self.assertEqual(r['l2'],'FALSE')
    def test_partial_zero_does_not_mean_match(self):
        # Normally such incomplete streams would be a protocol violation.
        with self.assertRaises(ValueError): self.parse(valid_log(p1w=168,p1m=0))
    def test_duplicate_applied_refused(self):
        b=valid_log(); withdup=b+b.splitlines(keepends=True)[0]
        with self.assertRaises(ValueError): self.parse(withdup)
    def test_duplicate_result_refused(self):
        b=valid_log(); withdup=b+b.splitlines(keepends=True)[-1]
        with self.assertRaises(ValueError): self.parse(withdup)
    def test_changed_bit_refused(self):
        b=valid_log().replace(b'class=BUF bit=0',b'class=BUF bit=1')
        with self.assertRaises(ValueError): self.parse(b)
    def test_changed_offset_refused(self):
        b=valid_log().replace(b',BUF,0,791,',b',BUF,0,792,')
        with self.assertRaises(ValueError): self.parse(b)
    def test_missing_applied_refused(self):
        with self.assertRaises(ValueError): self.parse(b'\n'.join(valid_log().splitlines()[1:])+b'\n')
    def test_same_old_new_bit_refused(self):
        with self.assertRaises(ValueError): self.parse(valid_log().replace(b'old=0 new=1',b'old=0 new=0'))
    def test_unknown_outcome_refused(self):
        with self.assertRaises(ValueError): self.parse(valid_log().replace(b'FINAL_OUTPUT_MATCH',b'UNKNOWN_OUTCOME'))
    def test_false_final_match_with_logit_error_refused(self):
        with self.assertRaises(ValueError): self.parse(valid_log(logits=1))
    def test_invalid_pool1_counter_refused(self):
        with self.assertRaises(ValueError): self.parse(valid_log(p1m=677))
    def test_invalid_pool2_counter_refused(self):
        with self.assertRaises(ValueError): self.parse(valid_log(p2m=201))
    def test_complete_pool2_mismatch_supported(self):
        r=self.parse(valid_log(p1m=0,p2m=1))
        self.assertEqual(r['l2'],'TRUE')
    def test_wrong_golden_label_refused(self):
        with self.assertRaises(ValueError): self.parse(valid_log(pred=3))
    def test_wrong_first_stage_refused(self):
        with self.assertRaises(ValueError): self.parse(valid_log().replace(b'POOL1',b'NONE'))
    def test_bad_result_field_count(self):
        with self.assertRaises(ValueError): self.parse(valid_log().replace(b',169,25,',b',169,'))
    def test_bad_requested_cycle(self):
        with self.assertRaises(ValueError): self.parse(valid_log().replace(b'requested=794',b'requested=795'))


class AuditOutput(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.d=Path(self.t.name)/'v4m_test_01'
        self.source={'schema':'mock','rows':[]}
        m.create(self.d,{'ok.json':b'abc\n'})
    def test_no_overwrite(self):
        with self.assertRaises(ValueError): m.create(self.d,{'ok.json':b'a'})
    def test_namespace(self):
        with self.assertRaises(ValueError): m.create(Path(self.t.name)/'evil_01',{'ok.json':b'a'})
    def test_hash_inventory(self):
        self.assertIn(m.sha(b'abc\n').encode(),(self.d/'SHA256SUMS.txt').read_bytes())
    def test_extra_artifact_refused(self):
        with self.assertRaises(ValueError): m.require({p.name for p in self.d.iterdir()}==auditor.EXPECTED,'Incomplete')
    def test_duplicate_json_key(self):
        with self.assertRaises(ValueError): m.json_strict(b'{"x":1,"x":2}')
    def test_canonical_lf(self):
        self.assertEqual(m.canonical({'x':1}),b'{\n  "x": 1\n}\n')
    def test_raw_record_does_not_contain_executable_commands(self):
        self.assertFalse(hasattr(m,'launch_xsim'))
        self.assertFalse(hasattr(m,'execute_full400'))


if __name__=='__main__': unittest.main()
