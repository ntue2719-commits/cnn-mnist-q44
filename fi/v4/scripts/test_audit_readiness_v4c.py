#!/usr/bin/env python3
"""Offline adversarial unit tests: V4c must fail closed before full400."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from audit_readiness_v4c import (
    SCHEMA, STATUS, FULL400_STATUS, REVIEW_COLUMNS,
    check_coverage, collect_v3b, collect_v4bb, render_report,
    resolved,
)
from audit_v4 import GROUPS
from audit_six_consumers_v4b import PROVED, PENDING, COLUMNS as B_COLUMNS
from pilot400_common import FIELDS, load_plan, verify_manifest, write_csv, sha256

ROOT = Path(__file__).resolve().parents[3]


def setup():
    plans = load_plan(ROOT / 'fi/config/pilot400_targets.csv',
                      ROOT / 'fi/config/fi_targets_candidates_v2.csv')
    manifest = verify_manifest(ROOT / 'fi/scenarios/pilot400.csv', plans)
    chosen = [row for row in manifest if row['sample_id'] == 'IMG0000']
    evidence = {}
    for row in chosen:
        tid = row['target_id']
        source = ('V4B_B_ORIGINAL' if tid in PROVED else
                  'V4B_C_ISOLATED' if tid in PENDING else 'V3B_ISOLATED')
        evidence[row['scenario_id']] = {**row,
            'source':source, 'outcome':'FINAL_OUTPUT_MATCH',
            'proof':'VERIFIED_SYNTHETIC_ONLY', 'log_sha256':'a' * 64}
    return plans, manifest, evidence


class FrozenCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plans, cls.manifest, cls.evidence = setup()

    def test_success_all_20_40(self):
        matrix = check_coverage(self.plans, self.manifest, self.evidence)
        self.assertEqual(len(matrix), 20)
        self.assertEqual({x['proof_source'] for x in matrix},
                         {'V3B_ISOLATED','V4B_B_ORIGINAL','V4B_C_ISOLATED'})
        self.assertTrue(all(row['full400_authorized']=='NO' for row in matrix))

    def test_schema_non_authorizing(self):
        self.assertIn('V4C', SCHEMA)
        self.assertIn('TWENTY', STATUS)
        self.assertIn('BLOCKED', FULL400_STATUS)
        self.assertIn('full400_authorized', REVIEW_COLUMNS)

    def test_dat0010_pair_bits_same_edge(self):
        rows = check_coverage(self.plans, self.manifest, self.evidence)
        d = next(r for r in rows if r['target_id']=='DAT_0010')
        self.assertEqual((d['config_a_bit'],d['config_a_offset'],
                          d['config_b_bit'],d['config_b_offset']),
                         ('0','1340','1','1340'))

    def test_frozen_class_balance(self):
        self.assertEqual({c:sum(r['target_class']==c for r in self.plans)
                          for c in ('CTL','BUF','MEM','DAT')},
                         {'CTL':5,'BUF':5,'MEM':5,'DAT':5})

    def test_one_missing_proof_rejected(self):
        evidence = deepcopy(self.evidence)
        evidence.pop('FI_P400_0001')
        with self.assertRaisesRegex(ValueError,'missing/extra'):
            check_coverage(self.plans, self.manifest, evidence)

    def test_one_extra_proof_rejected(self):
        evidence = deepcopy(self.evidence)
        evidence['FI_UNKNOWN'] = next(iter(evidence.values()))
        with self.assertRaisesRegex(ValueError,'missing/extra'):
            check_coverage(self.plans, self.manifest, evidence)

    def test_target_wrong_bit_rejected(self):
        evidence = deepcopy(self.evidence)
        evidence['FI_P400_0382']['bit_index'] = '0'
        with self.assertRaisesRegex(ValueError,'tuple differs'):
            check_coverage(self.plans, self.manifest, evidence)

    def test_target_wrong_offset_rejected(self):
        evidence = deepcopy(self.evidence)
        evidence['FI_P400_0001']['injection_offset'] = '126'
        with self.assertRaisesRegex(ValueError,'tuple differs'):
            check_coverage(self.plans, self.manifest, evidence)

    def test_wrong_candidate_source_rejected(self):
        evidence = deepcopy(self.evidence)
        evidence['FI_P400_0021']['source'] = 'V4B_C_ISOLATED'
        evidence['FI_P400_0022']['source'] = 'V4B_C_ISOLATED'
        with self.assertRaisesRegex(ValueError,'candidate evidence source'):
            check_coverage(self.plans, self.manifest, evidence)

    def test_wrong_historical_existing_source_rejected(self):
        evidence = deepcopy(self.evidence)
        evidence['FI_P400_0161']['source'] = 'V4B_C_ISOLATED'
        evidence['FI_P400_0162']['source'] = 'V4B_C_ISOLATED'
        with self.assertRaisesRegex(ValueError,'prior consumer source'):
            check_coverage(self.plans, self.manifest, evidence)

    def test_wrong_historical_new_source_rejected(self):
        evidence = deepcopy(self.evidence)
        evidence['FI_P400_0141']['source'] = 'V4B_B_ORIGINAL'
        evidence['FI_P400_0142']['source'] = 'V4B_B_ORIGINAL'
        with self.assertRaisesRegex(ValueError,'historical monitor source'):
            check_coverage(self.plans, self.manifest, evidence)

    def test_mixed_target_sources_rejected(self):
        evidence = deepcopy(self.evidence)
        evidence['FI_P400_0141']['source'] = 'V4B_B_ORIGINAL'
        with self.assertRaisesRegex(ValueError,'mixed provenance'):
            check_coverage(self.plans, self.manifest, evidence)

    def test_empty_consumer_proof_rejected(self):
        evidence = deepcopy(self.evidence)
        evidence['FI_P400_0341']['proof'] = ''
        with self.assertRaisesRegex(ValueError,'invalid consumer'):
            check_coverage(self.plans, self.manifest, evidence)

    def test_invalid_outcome_rejected(self):
        evidence = deepcopy(self.evidence)
        evidence['FI_P400_0341']['outcome'] = 'INVENTED_OUTCOME'
        with self.assertRaisesRegex(ValueError,'invalid consumer'):
            check_coverage(self.plans, self.manifest, evidence)

    def test_incomplete_hash_rejected(self):
        evidence = deepcopy(self.evidence)
        evidence['FI_P400_0201']['log_sha256'] = 'abc'
        with self.assertRaisesRegex(ValueError,'invalid consumer'):
            check_coverage(self.plans, self.manifest, evidence)

    def test_missing_target_coverage_rejected(self):
        plans = deepcopy(self.plans)
        plans.pop()
        with self.assertRaisesRegex(ValueError,'20 target plans'):
            check_coverage(plans, self.manifest, self.evidence)

    def test_manifest_deleted_row_rejected(self):
        manifest = deepcopy(self.manifest)
        manifest.pop()
        with self.assertRaisesRegex(ValueError,'400 manifest rows'):
            check_coverage(self.plans, manifest, self.evidence)

    def test_manifest_changed_pair_rejected(self):
        manifest = deepcopy(self.manifest)
        row = next(r for r in manifest if r['scenario_id']=='FI_P400_0382')
        row['bit_index']='0'
        evidence = deepcopy(self.evidence)
        evidence['FI_P400_0382']['bit_index']='0'
        with self.assertRaisesRegex(ValueError,'frozen target bit/time pair'):
            check_coverage(self.plans, manifest, evidence)

    def test_frozen_status_unknown_rejected(self):
        plans = deepcopy(self.plans)
        next(r for r in plans if r['target_id']=='CTL_0001')['evidence_status']='UNREVIEWED'
        with self.assertRaisesRegex(ValueError,'unfamiliar frozen'):
            check_coverage(plans, self.manifest, self.evidence)

    def test_matrix_never_marks_promotion(self):
        rows = check_coverage(self.plans, self.manifest, self.evidence)
        self.assertEqual({r['automatic_promotion'] for r in rows}, {'NO'})


class SavedEvidenceTests(unittest.TestCase):
    def test_existing_consumer_csv_positive(self):
        plans, manifest, _ = setup()
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            rows=[]
            for row in manifest:
                if row['sample_id']=='IMG0000' and row['target_id'] in PROVED:
                    rows.append({**row,'outcome':'FINAL_OUTPUT_MATCH',
                                 'consumer_evidence':PROVED[row['target_id']],
                                 'source_marker':'FAKE_UNIT_TEST',
                                 'log_sha256':'b'*64})
            for row in manifest:
                if row['sample_id']=='IMG0000' and row['target_id'] in PENDING:
                    rows.append({**row,'outcome':'FINAL_OUTPUT_MATCH',
                                 'consumer_evidence':'NOT_PROVED_IN_V4B_A_LOGS',
                                 'source_marker':'NONE','log_sha256':'b'*64})
            write_csv(folder/'existing_consumer_evidence.csv',B_COLUMNS,rows)
            (folder/'existing_consumer_audit.json').write_text(json.dumps({
                'direct_consumer_scenarios':6,
                'direct_consumer_targets':sorted(PROVED),
                'csv_sha256':sha256(folder/'existing_consumer_evidence.csv')}))
            got=collect_v4bb(folder/'existing_consumer_audit.json',manifest)
            self.assertEqual(len(got),6)
            self.assertEqual({r['target_id'] for r in got.values()},set(PROVED))

    def test_existing_consumer_csv_tampering_rejected(self):
        _,manifest,_=setup()
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            (folder/'existing_consumer_evidence.csv').write_text('wrong,data\n')
            (folder/'existing_consumer_audit.json').write_text(json.dumps({
                'direct_consumer_scenarios':6,
                'direct_consumer_targets':sorted(PROVED),
                'csv_sha256':'f'*64}))
            with self.assertRaisesRegex(ValueError,'SHA256 mismatch'):
                collect_v4bb(folder/'existing_consumer_audit.json',manifest)

    def test_reviewer_report_contains_blocker(self):
        plans,manifest,evidence=setup()
        matrix=check_coverage(plans,manifest,evidence)
        report=render_report(matrix,{'V3B_ISOLATED':28,'V4B_B_ORIGINAL':6,
                                     'V4B_C_ISOLATED':6},
                             {'FINAL_OUTPUT_MATCH':40},'a'*64)
        self.assertIn('BLOCKED',report)
        self.assertIn('NOT 400',report)
        self.assertIn('DAT_0010',report)
        self.assertIn('10-sample fault-free',report)

    def test_v3b_missing_log_rejected(self):
        plans,manifest,_=setup()
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            fake=type('G',(),{'name':'CTL','dirname':'fake',
                              'selected':'selected.csv','targets':('CTL_0012',)})()
            rows=[r for r in manifest if r['sample_id']=='IMG0000' and
                  r['target_id']=='CTL_0012']
            target=base/'fake';target.mkdir()
            write_csv(target/'selected.csv',FIELDS,rows)
            columns=(*FIELDS,'consumer_proof','outcome','log_sha256')
            write_csv(target/'consumer_validation.csv',columns,
                      [{**r,'consumer_proof':'VERIFIED_TEST','outcome':'FINAL_OUTPUT_MATCH',
                        'log_sha256':'f'*64} for r in rows])
            with patch('audit_readiness_v4c.GROUPS',(fake,)):
                with self.assertRaisesRegex(ValueError,'consumer proof/hash'):
                    collect_v3b(ROOT,base,manifest)

    def test_no_replace_prior_output(self):
        src=Path('fi/results/v4c_readiness_audit_01')
        self.assertFalse(src.is_absolute())
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            self.assertEqual(resolved(p,Path('a/b')), (p/'a/b').resolve())


if __name__ == '__main__':
    unittest.main()
