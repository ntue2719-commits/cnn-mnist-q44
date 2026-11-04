#!/usr/bin/env python3
"""V4J copy-only bundle unit tests; ALL sources/tools/assets are mock fixtures.

Never runs Vivado, XSim, any shell command, or writes into historical evidence.
"""
from __future__ import annotations

from argparse import Namespace
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0,str(HERE))
import production_source_snapshot_v4i as v4i
import audit_source_snapshot_v4i as v4i_audit
import production_source_bundle_v4j as j
import audit_source_bundle_v4j as a
from test_source_snapshot_v4i import Fixture,save_json


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f=Fixture(Path(self.tmp.name))
        self.repo=self.f.repo
        self.inventory=self.f.results/'v4i_source_snapshot_01'
        self.inventory.mkdir()
        self.inventory_audit=self.f.results/'v4i_source_snapshot_audit_01'
        self.inventory_audit.mkdir()
        manifest,receipt=self.f.audit_objects()
        save_json(self.inventory/'v4i_source_inventory.json',manifest)
        save_json(self.inventory/'v4i_source_inventory_receipt.json',receipt)
        audit={
            'schema':v4i_audit.AUDIT_SCHEMA,
            'source_inventory_sha256':v4i.sha_file(self.inventory/'v4i_source_inventory.json'),
            'source_inventory_receipt_sha256':v4i.sha_file(self.inventory/'v4i_source_inventory_receipt.json'),
            'source_identity_sha256':manifest['source_identity_sha256'],
            'compiled_sources_rehashed':manifest['source_count'],
            'v4f4f_asset_and_source_pins_rehashed':manifest['v4f4f_input_pin_count'],
            'frozen_rehashed':4,
            'toolchain_files_rehashed':len(manifest['toolchain_sha256']),
            'independently_qualified_g6':False,
            'read_only_rehash_replay':True,
            'build_once_executed':False,
            'new_xsim_runs':0,
            'full400_authorized':False,
            'release_gate':v4i.STOP,
        }
        save_json(self.inventory_audit/'v4i_source_inventory_audit.json',audit)
        self.bundle=self.f.results/'v4j_source_bundle_01'
        self.audit_out=self.f.results/'v4j_source_bundle_audit_01'

    def context(self):
        with patch.object(v4i,'FROZEN',self.f.frozen):
            return j.proof_context(self.repo,self.f.project,self.f.vivado,self.f.prj,
                                   self.f.evidence,self.inventory,self.inventory_audit)

    def model(self):
        context=self.context()
        return j.make_manifest(context,j.source_records(context,frozen=self.f.frozen))

    def capture(self):
        m=self.model()
        receipt=j.capture(self.repo,self.bundle,m)
        return m,receipt

    def verify(self):
        return a.verify_bundle(self.bundle,self.model())

    def args(self,*,mode='audit',out=None):
        return Namespace(
            mode=mode,execute=False,approve=False,repo=self.repo,
            project=self.f.project,vivado=self.f.vivado,prj=self.f.prj,
            inventory_dir=self.inventory,inventory_audit_dir=self.inventory_audit,
            bundle_dir=self.bundle,out_dir=out or self.audit_out,
            **{k+'_dir':self.f.evidence[k] for k in
               ('source','review','history','rehearsal','plan','adapter','adapter_audit')})

    def denied_context(self):
        with self.assertRaises(ValueError): self.context()

    def denied_audit(self):
        with self.assertRaises(ValueError): self.verify()

    def test_model_pins_and_source_count(self):
        m=self.model()
        self.assertEqual(m['compiled_source_count'],28)
        self.assertEqual(m['v4f4f_pin_count'],60)
        self.assertGreaterEqual(m['input_record_count'],86)
        self.assertFalse(m['vivado_build_once_executed'])
        self.assertFalse(m['full400_authorized'])

    def test_content_roles_and_28_compile_sources(self):
        m=self.model()
        self.assertEqual(len(m['compile_order']),28)
        self.assertIn('COMPILED_GENERATED_GLBL', {r for f in m['files'] for r in f['roles']})
        self.assertIn('PROJECT_XPR',{r for f in m['files'] for r in f['roles']})
        self.assertIn('COMPILE_PRJ',{r for f in m['files'] for r in f['roles']})

    def _pin_legacy_crlf_plan_in_v4i_parent(self):
        """Model the preexisting V4I receipts of a Windows V4f4F text writer."""
        path = self.f.file('plan')
        raw = path.read_bytes()
        self.assertNotIn(b'\r\n', raw)
        path.write_bytes(raw.replace(b'\n', b'\r\n'))
        mpath = self.inventory/'v4i_source_inventory.json'
        rpath = self.inventory/'v4i_source_inventory_receipt.json'
        apath = self.inventory_audit/'v4i_source_inventory_audit.json'
        manifest = v4i.read_object(mpath)
        manifest['v4f4f_plan_sha256'] = v4i.sha_file(path)
        save_json(mpath, manifest)
        receipt = v4i.read_object(rpath)
        receipt['source_inventory_sha256'] = v4i.sha_file(mpath)
        save_json(rpath, receipt)
        audit = v4i.read_object(apath)
        audit['source_inventory_sha256'] = v4i.sha_file(mpath)
        audit['source_inventory_receipt_sha256'] = v4i.sha_file(rpath)
        save_json(apath, audit)
        return path

    def test_legacy_v4f4f_plan_pure_crlf_read_only(self):
        path = self._pin_legacy_crlf_plan_in_v4i_parent()
        before = path.read_bytes()
        plan = j.read_pinned_historical_v4f4f_plan(path, v4i.sha(before))
        self.assertEqual(plan['planned_count'],400)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(self.context()['manifest']['v4f4f_plan_sha256'],v4i.sha(before))

    def test_legacy_crlf_full_capture_audit_roundtrip(self):
        path = self._pin_legacy_crlf_plan_in_v4i_parent()
        before = path.read_bytes()
        m, receipt = self.capture()
        self.assertEqual(m['v4f4f_pin_count'],60)
        self.verify()
        with patch.object(v4i,'FROZEN',self.f.frozen):
            res = a.run_audit(self.args())
        self.assertEqual(res['content_blobs_rehashed'],m['unique_blob_count'])
        self.assertEqual(path.read_bytes(),before)
        self.assertFalse(receipt['full400_authorized'])

    def test_legacy_v4f4f_plan_canonical_lf_still_accepted(self):
        path = self.f.file('plan')
        raw = path.read_bytes()
        self.assertEqual(j.read_pinned_historical_v4f4f_plan(path, v4i.sha(raw)),
                         v4i.read_object(path))

    def test_legacy_v4f4f_plan_stale_raw_sha_denied(self):
        path = self._pin_legacy_crlf_plan_in_v4i_parent()
        with self.assertRaisesRegex(ValueError,'raw SHA256 drift'):
            j.read_pinned_historical_v4f4f_plan(path,'a'*64)

    def test_legacy_v4f4f_plan_mixed_endings_denied(self):
        path = self.f.file('plan')
        raw = path.read_bytes()
        self.assertGreater(raw.count(b'\n'),1)
        raw = raw.replace(b'\n',b'\r\n',1)
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError,'neither canonical LF nor pure canonical CRLF'):
            j.read_pinned_historical_v4f4f_plan(path,v4i.sha(raw))

    def test_legacy_v4f4f_plan_bom_denied(self):
        path = self.f.file('plan')
        raw = b'\xef\xbb\xbf' + path.read_bytes()
        path.write_bytes(raw)
        with self.assertRaises((ValueError,UnicodeDecodeError)):
            j.read_pinned_historical_v4f4f_plan(path,v4i.sha(raw))

    def test_legacy_v4f4f_plan_duplicate_key_denied(self):
        path = self.f.file('plan')
        raw = b'{"planned_count":400,"planned_count":400}'
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError,'Duplicate JSON key'):
            j.read_pinned_historical_v4f4f_plan(path,v4i.sha(raw))

    def test_legacy_v4f4f_plan_noncanonical_whitespace_denied(self):
        path = self.f.file('plan')
        raw = path.read_bytes() + b'\r\n'
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError,'neither canonical LF nor pure canonical CRLF'):
            j.read_pinned_historical_v4f4f_plan(path,v4i.sha(raw))

    def test_legacy_v4f4f_plan_exception_restricted_to_plan_only(self):
        wrong = self.inventory/'v4i_source_inventory.json'
        with self.assertRaisesRegex(ValueError,'restricted to V4f4F plan'):
            j.read_pinned_historical_v4f4f_plan(wrong,v4i.sha_file(wrong))
        raw = wrong.read_bytes().replace(b'\n',b'\r\n')
        wrong.write_bytes(raw)
        with self.assertRaisesRegex(ValueError,'Noncanonical UTF-8 LF JSON'):
            j.exact_json(wrong)

    def test_legacy_v4f4f_plan_release_forgery_denied(self):
        self._pin_legacy_crlf_plan_in_v4i_parent()
        path = self.f.file('plan')
        obj = v4i.read_object(path)
        obj['full400_authorized'] = True
        path.write_bytes(v4i.canonical(obj).replace(b'\n',b'\r\n'))
        self.denied_context()

    def test_source_bundle_roundtrip(self):
        m,r=self.capture()
        expected,actual=self.verify()
        self.assertEqual(expected,m)
        self.assertEqual(actual,r)
        self.assertEqual(r['unique_blobs_rehashed'],m['unique_blob_count'])

    def test_source_bundle_canonical_lf(self):
        self.capture()
        for name in j.BUNDLE_FILES:
            raw=(self.bundle/name).read_bytes()
            self.assertNotIn(b'\r\n',raw)

    def test_source_bundle_no_tool_copy_or_build(self):
        m,_=self.capture()
        self.assertFalse(m['vivado_toolchain_copied'])
        self.assertFalse(m['compile_prj_rewritten'])
        self.assertFalse(m['portable_compiled_project_proven'])

    def test_same_byte_dedup(self):
        m=self.model()
        counts=[f['sha256'] for f in m['files']]
        self.assertLessEqual(m['unique_blob_count'],len(counts))
        self.assertEqual(m['unique_blob_count'],len(set(counts)))

    def test_v4i_parent_input_identity_link(self):
        m=self.model()
        self.assertEqual(m['v4i_source_inventory_sha256'],
                         v4i.sha_file(self.inventory/'v4i_source_inventory.json'))
        self.assertEqual(m['source_identity_sha256'],self.context()['manifest']['source_identity_sha256'])

    def test_full_auditor_runs_without_execution(self):
        m,_=self.capture()
        with patch.object(v4i,'FROZEN',self.f.frozen), patch.object(j.v4i,'FROZEN',self.f.frozen):
            # V4J's frozen source list is taken from the V4I canonical table;
            # patch the test-only fixture table as it contains mock bytes.
            res=a.run_audit(self.args())
        self.assertTrue((self.audit_out/'v4j_source_bundle_audit.json').is_file())
        self.assertEqual(res['compiled_sources_rehashed'],28)
        self.assertEqual(res['content_blobs_rehashed'],m['unique_blob_count'])
        self.assertFalse(res['full400_authorized'])

    def test_bundle_overwrite_refused(self):
        m,_=self.capture()
        with self.assertRaises(ValueError): j.capture(self.repo,self.bundle,m)

    def test_capture_preexisting_inflight_refused(self):
        stage=self.bundle.with_name('.'+self.bundle.name+'.inflight')
        stage.mkdir()
        with self.assertRaises(ValueError): j.capture(self.repo,self.bundle,self.model())
        self.assertTrue(stage.is_dir())
        self.assertFalse(self.bundle.exists())

    def test_bad_outside_output_namespace_refused(self):
        with self.assertRaises(ValueError): j.artifact_dir(self.repo,Path('../outside'), 'v4j_',existing=False)

    def test_existing_audit_output_refused(self):
        self.capture()
        self.audit_out.mkdir()
        with self.assertRaises(ValueError): a.run_audit(self.args())

    def test_wrong_mode_denied_before_io(self):
        for mode in ('build','execute','full400'):
            with self.subTest(mode=mode),self.assertRaisesRegex(ValueError,'DENIED BEFORE IO'):
                j.check(mode=='capture' and False,'DENIED BEFORE IO')
            with self.subTest(mode=mode),self.assertRaisesRegex(ValueError,'DENIED BEFORE IO'):
                a.run_audit(self.args(mode=mode))

    def test_audit_full400_flag_forgery_denied(self):
        self.capture()
        path=self.bundle/j.BUNDLE_FILES[0]
        obj=j.exact_json(path)
        obj['full400_authorized']=True
        path.write_bytes(v4i.canonical(obj))
        self.denied_audit()

    def test_audit_scope_forgery_denied(self):
        self.capture()
        path=self.bundle/j.BUNDLE_FILES[0]
        obj=j.exact_json(path)
        obj['vivado_build_once_executed']=True
        path.write_bytes(v4i.canonical(obj))
        self.denied_audit()

    def test_source_rtl_drift_before_capture(self):
        self.f.hdl_paths[0].write_bytes(b'corrupted design')
        self.denied_context()

    def test_source_glbl_drift_before_capture(self):
        self.f.hdl_paths[-1].write_bytes(b'corrupted glbl')
        self.denied_context()

    def test_source_prj_drift_before_capture(self):
        self.f.prj.write_bytes(b'broken .prj')
        self.denied_context()

    def test_source_vivado_drift_before_capture(self):
        self.f.vivado.write_bytes(b'changed tool')
        self.denied_context()

    def test_source_asset_drift_before_capture(self):
        (self.repo/'fi/mock_assets/data_001.hex').write_bytes(b'changed')
        self.denied_context()

    def test_snapshot_live_source_drift_after_capture(self):
        self.capture()
        self.f.hdl_paths[0].write_bytes(b'changed after copy')
        self.denied_audit()

    def test_source_blob_corruption_rejected(self):
        self.capture()
        p=next(self.bundle.glob('blobs/*/*'))
        p.write_bytes(b'corrupt')
        self.denied_audit()

    def test_source_blob_missing_rejected(self):
        self.capture()
        p=next(self.bundle.glob('blobs/*/*'))
        p.unlink()
        self.denied_audit()

    def test_orphan_blob_rejected(self):
        self.capture()
        p=self.bundle/'blobs/aa'/('a'*64)
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(b'orphan')
        self.denied_audit()

    def test_crlf_manifest_rejected(self):
        self.capture()
        p=self.bundle/j.BUNDLE_FILES[0]
        p.write_bytes(p.read_bytes().replace(b'\n',b'\r\n'))
        self.denied_audit()

    def test_duplicate_json_key_rejected(self):
        self.capture()
        p=self.bundle/j.BUNDLE_FILES[1]
        p.write_bytes(b'{"schema":1,"schema":2}')
        self.denied_audit()

    def test_change_receipt_digest_rejected(self):
        self.capture()
        p=self.bundle/j.BUNDLE_FILES[1]
        obj=j.exact_json(p)
        obj['bundle_manifest_sha256']='f'*64
        p.write_bytes(v4i.canonical(obj))
        self.denied_audit()

    def test_receipt_link_to_old_v4i_refused(self):
        self.capture()
        p=self.bundle/j.BUNDLE_FILES[1]
        obj=j.exact_json(p)
        obj['v4i_source_inventory_audit_sha256']='a'*64
        p.write_bytes(v4i.canonical(obj))
        self.denied_audit()

    def test_extra_root_file_refused(self):
        self.capture()
        (self.bundle/'fake.txt').write_bytes(b'hello')
        self.denied_audit()

    def test_extra_directory_refused(self):
        self.capture()
        (self.bundle/'unexpected').mkdir()
        self.denied_audit()

    def test_missing_v4i_audit_refused(self):
        (self.inventory_audit/'v4i_source_inventory_audit.json').unlink()
        self.denied_context()

    def test_v4i_audit_forged_approval_refused(self):
        p=self.inventory_audit/'v4i_source_inventory_audit.json'
        obj=j.exact_json(p)
        obj['full400_authorized']=True
        p.write_bytes(v4i.canonical(obj))
        self.denied_context()

    def test_v4i_inventory_modified_refused(self):
        p=self.inventory/'v4i_source_inventory.json'
        obj=j.exact_json(p)
        obj['source_identity_sha256']='f'*64
        p.write_bytes(v4i.canonical(obj))
        self.denied_context()

    def test_source_plan_pins_change_refused(self):
        p=self.f.file('plan')
        obj=v4i.read_object(p)
        obj['input_fingerprint']='a'*64
        save_json(p,obj)
        self.denied_context()

    def test_invalid_record_size_refused(self):
        m=self.model()
        m['files'][0]['bytes']=-1
        with self.assertRaises(ValueError): j.verify_record_shape(m)

    def test_path_traversal_blob_refused(self):
        m=self.model()
        m['files'][0]['blob']='../../bad'
        with self.assertRaises(ValueError): j.verify_record_shape(m)

    def test_duplicate_role_refused(self):
        m=self.model()
        m['files'][0]['roles']=['DUP','DUP']
        with self.assertRaises(ValueError): j.verify_record_shape(m)

    def test_fake_tool_copied_claim_refused(self):
        m=self.model()
        m['vivado_toolchain_copied']=True
        with self.assertRaises(ValueError): j.verify_record_shape(m)

    def test_fake_e3_outcome_claim_refused(self):
        m=self.model()
        m['fi_outcomes_committed']=1
        with self.assertRaises(ValueError): j.verify_record_shape(m)

    def test_canonical_manifest_deterministic(self):
        m1=self.model();m2=self.model()
        self.assertEqual(v4i.canonical(m1),v4i.canonical(m2))

    def test_positive_capture_and_audit_cli_entrypoints(self):
        common=['--repo',str(self.repo),
                '--project',str(self.f.project),
                '--vivado',str(self.f.vivado),
                '--prj',str(self.f.prj)]
        with patch.object(v4i,'FROZEN',self.f.frozen):
            with patch.object(sys,'argv',['capture','--mode','capture']+common):
                j.cli()
            self.assertTrue((self.bundle/j.BUNDLE_FILES[0]).is_file())
            with patch.object(sys,'argv',['audit','--mode','audit']+common):
                a.cli()
        self.assertTrue((self.audit_out/'v4j_source_bundle_audit.json').is_file())

    def test_source_changes_after_early_copy_before_publish_refused(self):
        m=self.model()
        old_copy=j.copy_one
        mutate=self.f.hdl_paths[0]
        used=[False]
        def changed(src,dst,digest,size):
            old_copy(src,dst,digest,size)
            if not used[0]:
                used[0]=True
                mutate.write_bytes(mutate.read_bytes()+b'// late drift')
        with patch.object(j,'copy_one',side_effect=changed):
            with self.assertRaises(ValueError):
                j.capture(self.repo,self.bundle,m)
        self.assertFalse(self.bundle.exists())
        self.assertTrue(self.bundle.with_name('.'+self.bundle.name+'.inflight').exists())

    def test_capture_original_input_untouched(self):
        before={row['original_path']:v4i.sha_file(Path(row['original_path']))
                for row in self.model()['files']}
        self.capture()
        self.assertEqual(before,{path:v4i.sha_file(Path(path)) for path in before})

    def test_cli_build_execute_approve_denied_before_io(self):
        for args in (['--mode','build'], ['--mode','execute'],
                     ['--mode','full400'], ['--execute'], ['--approve']):
            with self.subTest(args=args),patch.object(sys,'argv',['capture','--project',str(self.f.project),'--vivado',str(self.f.vivado)]+args),                 self.assertRaisesRegex(SystemExit,'DENIED BEFORE IO'):
                j.cli()

    def test_auditor_cli_execution_denied_before_io(self):
        for args in (['--mode','build'], ['--mode','execute'],
                     ['--mode','full400'], ['--execute'], ['--approve']):
            with self.subTest(args=args),patch.object(sys,'argv',['audit','--project',str(self.f.project),'--vivado',str(self.f.vivado)]+args),                 self.assertRaisesRegex(SystemExit,'DENIED BEFORE IO'):
                a.cli()

    def test_invalid_file_record_type_refused(self):
        m=self.model()
        m['files'][0]='not a dict'
        with self.assertRaises(ValueError): j.verify_record_shape(m)

    def test_symlink_bundle_blob_rejected_when_supported(self):
        self.capture()
        p=next(self.bundle.glob('blobs/*/*'))
        other=self.repo/'some_other_file.txt'
        other.write_bytes(b'linked')
        try:
            p.unlink()
            p.symlink_to(other)
        except (OSError,NotImplementedError):
            self.skipTest('symlink creation denied by host')
        self.denied_audit()


if __name__=='__main__':
    unittest.main()
