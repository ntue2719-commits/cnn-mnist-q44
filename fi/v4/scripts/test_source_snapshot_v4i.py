#!/usr/bin/env python3
"""V4I offline tests; all project, HDL, toolchain and evidence are mock bytes."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from argparse import Namespace

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import production_source_snapshot_v4i as p
import audit_source_snapshot_v4i as a


def save(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def save_json(path: Path, obj: dict) -> Path:
    return save(path, p.canonical(obj))


class Fixture:
    def __init__(self, root: Path):
        # Match the CLI contract, which always resolves --repo.  This
        # avoids noncanonical TemporaryDirectory spellings on Windows.
        self.repo = (root / 'cnn-mnist-q44-rtl').resolve()
        self.results = self.repo / 'fi/results'
        self.results.mkdir(parents=True)
        self.project = save(root / 'Simulation/Simulation.xpr', b'<project name="test"/>\n')
        self.prj = self.results / 'pilot400_v3a_28_01/source_vlog.prj'
        self.vivado = save(root / 'Vivado/2022.2/bin/vivado.bat', b'@echo off\nrem fake\n')
        self.tools = [self.vivado]
        for name in p.TOOL_NAMES:
            self.tools.append(save(self.vivado.parent / name, ('mock '+name).encode()))
        self.evidence = {}
        dirs = {
            'source': 'v4f4f_source_preflight_01',
            'review': 'v4f4f_release_review_01',
            'history': 'v4f4f_history_audit_01',
            'rehearsal': 'v4f4f_rehearsal_audit_01',
            'adapter': 'v4h_exec_adapter_plan_01',
            'adapter_receipt': 'v4h_exec_adapter_plan_01',
            'adapter_audit': 'v4h_exec_adapter_audit_01',
            'plan': 'v4f4f_plan_01',
        }
        for key, name in dirs.items():
            self.evidence[key] = self.results / name
            self.evidence[key].mkdir(exist_ok=True)
        self.frozen = {}
        for key in p.FROZEN:
            f = save(self.repo / key, ('fixture:'+key+'\n').encode())
            self.frozen[key] = p.sha_file(f)
        self.hdl_paths = []
        rtl_names = sorted(p.NEEDED_RTL) + [f'mock_rtl_{i:02d}.v' for i in range(18)]
        for name in rtl_names:
            src = save(root / 'Simulation/Simulation.srcs/sources_1/new' / name,
                       ('module '+name.replace('.v','').replace('-','_')+'; endmodule\n').encode())
            self.hdl_paths.append(src.resolve())
        self.hdl_paths.append((self.repo / 'fi/tb/tb_cnn_mnist_fi_pilot400.v').resolve())
        self.hdl_paths.append(save(root / 'Simulation/Simulation.sim/sim_1/behav/xsim/glbl.v',
                                   b'module glbl; endmodule\n').resolve())
        prj_data = ('# compile sources\nverilog xil_defaultlib  \\\n' +
                    ' \\\n'.join('"'+x.as_posix()+'"' for x in self.hdl_paths) +
                    '\n\nnosort\n').encode()
        save(self.prj, prj_data)
        self.pins = {}
        for rel in self.frozen:
            f = self.repo / rel
            self.pins[str(f.resolve())] = p.sha_file(f)
        for i in range(56):
            f = save(self.repo / f'fi/mock_assets/data_{i:03d}.hex', bytes([i % 255, 9, 4]))
            self.pins[str(f.resolve())] = p.sha_file(f)
        self.input_fingerprint = p.sha(p.canonical(self.pins))
        self._write_inputs()

    def file(self, key: str) -> Path:
        return self.evidence[key] / p.FILES[key]

    def _write_inputs(self):
        s = {
            'schema': p.SOURCE_SCHEMA,
            'source_preflight': 'READONLY_VERIFIED_NO_COMPILE_ELAB_XSIM',
            'source_authority': 'VIVADO_XPR_PINNED_V3A_PRJ',
            'project_sha256': p.sha_file(self.project),
            'source_prj_sha256': p.sha_file(self.prj),
            'source_hashes': {str(f): p.sha_file(f) for f in self.hdl_paths},
            'rtl_count': 26,
            'toolchain_sha256': {str(x.resolve()): p.sha_file(x) for x in self.tools},
            'built_snapshot': False, 'new_xsim_runs': 0,
            'full400_authorized': False, 'G6_production': 'NOT_QUALIFIED',
            'G7': 'PREPARED_NOT_APPROVED',
        }
        save_json(self.file('source'), s)
        h = {'schema': 'CNN_PEP_V4F4F_EIGHT_ATTEMPT_ZIP_RECHECK_V1',
             'attempt_slots_rechecked': 8,'raw_artifacts_sha256_rechecked': 590,
             'bounded_valid_fi_results': 2,'historical_not_reproduced_slots': [3,7],
             'full400_authorized': False}
        save_json(self.file('history'), h)
        reh = {'schema': 'CNN_PEP_V4F4F_INDEPENDENT_REHEARSAL_AUDIT_V1',
               'synthetic_events_rehashed': 400, 'G6_production': 'NOT_QUALIFIED',
               'full400_authorized': False}
        save_json(self.file('rehearsal'), reh)
        r = {'schema': p.RELEASE_SCHEMA, 'full400_runner_qualified': False,
             'full400_authorized': False, 'full400_executed': False,
             'G0_G7_signed_authorization': False, 'new_xsim_runs_during_audit': 0,
             'read_only_evidence_refs': {
                 label: {'status': 'RECEIPT_PRESENT_NOT_RELEASE_AUTHORIZATION',
                         'receipt_sha256': p.sha_file(self.file(key))}
                 for label, key in (('G0_SOURCE','source'),('G6_BOUNDED_HISTORY','history'),
                                    ('G6_400_SYNTHETIC','rehearsal'))}}
        save_json(self.file('review'), r)
        plan = {'planned_count': 400, 'input_pins': self.pins,
                'input_fingerprint': self.input_fingerprint, 'full400_authorized': False}
        save_json(self.file('plan'), plan)
        adapter = {'schema': p.ADAPTER_SCHEMA, 'frozen_scenarios': 400,
                   'batch_count': 16, 'batch_size': 25,
                   'source_input_fingerprint': self.input_fingerprint,
                   'source_snapshot_built': False, 'new_xsim_runs': 0,
                   'G6_production_qualified': False, 'G7_scientific_approved': False,
                   'full400_authorized': False, 'full400_executed': False,
                   'invocations': [
                       {'scenario_id': f'FI_P400_{i:04d}', 'ready_to_execute': False,
                        'checkpoint_committed': False, 'design_outcome_observed': False}
                       for i in range(1,401)]}
        save_json(self.file('adapter'), adapter)
        ar = {'adapter_plan_sha256': p.sha_file(self.file('adapter')),
              'full400_authorized': False, 'new_xsim_runs': 0}
        save_json(self.file('adapter_receipt'), ar)
        aa = {'schema': p.ADAPTER_AUDIT_SCHEMA,
              'adapter_sha256': p.sha_file(self.file('adapter')),
              'adapter_receipt_sha256': p.sha_file(self.file('adapter_receipt')),
              'mapped_contracts_verified': 400, 'future_artifact_paths_verified': 3200,
              'new_xsim_runs': 0, 'build_once_executed': False,
              'G6_production_qualified': False, 'full400_authorized': False}
        save_json(self.file('adapter_audit'), aa)

    def collect(self):
        return p.collect(self.repo, self.project, self.vivado, self.prj,
                         self.evidence, frozen=self.frozen)

    def audit_objects(self):
        m = self.collect()
        raw = p.canonical(m)
        receipt = {
            'schema': p.RECEIPT_SCHEMA,
            'created_utc': datetime.now(timezone.utc).isoformat(),
            'source_inventory_sha256': p.sha(raw),
            'source_identity_sha256': m['source_identity_sha256'],
            'source_preflight_receipt_sha256': m['source_preflight_receipt_sha256'],
            'adapter_independent_audit_sha256': m['adapter_independent_audit_sha256'],
            'source_snapshot_materialized': False, 'build_once_executed': False,
            'new_xsim_runs': 0, 'full400_authorized': False, 'release_gate': p.STOP}
        return m, receipt

    def mutate(self, key: str, change):
        obj = p.read_object(self.file(key))
        change(obj)
        save_json(self.file(key), obj)
        return obj


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = Fixture(Path(self.tmp.name))
    def tearDown(self):
        self.tmp.cleanup()
    def denied(self):
        with self.assertRaises(ValueError): self.fixture.collect()

    def test_valid_28_compiled_sources(self):
        m = self.fixture.collect()
        self.assertEqual(m['source_count'], 28)
        self.assertEqual(m['rtl_count'], 26)
        self.assertEqual(m['v4f4f_input_pin_count'], 60)
        self.assertFalse(m['build_once_executed'])

    def test_exact_three_parents(self):
        m = self.fixture.collect()
        self.assertEqual(m['release_gap_receipt_sha256'], p.sha_file(self.fixture.file('review')))
        self.assertEqual(m['history_receipt_sha256'], p.sha_file(self.fixture.file('history')))
        self.assertEqual(m['adapter_independent_audit_sha256'], p.sha_file(self.fixture.file('adapter_audit')))

    def test_audit_reconstruction_equal(self):
        m,receipt = self.fixture.audit_objects()
        a.audit_model(m, receipt, p.sha(p.canonical(m)), self.fixture.collect())

    def test_source_json_can_be_legacy_crlf(self):
        path = self.fixture.file('source')
        path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
        self.fixture.mutate('review', lambda r: r['read_only_evidence_refs']['G0_SOURCE'].update(
            receipt_sha256=p.sha_file(path)))
        self.fixture.collect()

    def test_new_v4i_canonical_bytes(self):
        m, receipt = self.fixture.audit_objects()
        self.assertNotIn(b'\r\n', p.canonical(m))
        self.assertNotIn(b'\r\n', p.canonical(receipt))

    def test_detect_rtl_drift(self):
        file = self.fixture.hdl_paths[0]
        file.write_bytes(file.read_bytes()+b'// drift\n')
        self.denied()

    def test_detect_glbl_drift(self):
        file = self.fixture.hdl_paths[-1]
        file.write_bytes(file.read_bytes()+b'// glbl drift\n')
        self.denied()

    def test_detect_project_drift(self):
        self.fixture.project.write_bytes(b'new project')
        self.denied()

    def test_detect_compile_prj_drift(self):
        self.fixture.prj.write_bytes(self.fixture.prj.read_bytes()+b'# new line\n')
        self.denied()

    def test_detect_vivado_tool_drift(self):
        self.fixture.vivado.write_bytes(b'changed tool')
        self.denied()

    def test_detect_xsim_tool_drift(self):
        self.fixture.tools[-1].write_bytes(b'changed xsim')
        self.denied()

    def test_detect_sample_asset_drift(self):
        file = self.fixture.repo / 'fi/mock_assets/data_010.hex'
        file.write_bytes(b'changed input')
        self.denied()

    def test_detect_frozen_file_drift(self):
        file = self.fixture.repo / 'fi/config/pilot400_targets.csv'
        file.write_bytes(b'changed frozen')
        self.denied()

    def test_missing_glbl_compiled(self):
        self.fixture.hdl_paths[-1].unlink()
        self.denied()

    def test_missing_tb_compiled(self):
        file = self.fixture.repo / 'fi/tb/tb_cnn_mnist_fi_pilot400.v'
        file.unlink()
        self.denied()

    def test_compile_duplicate_entry_refused(self):
        file = self.fixture.prj
        file.write_bytes(file.read_bytes().replace(b'\nnosort',
                              ('"'+self.fixture.hdl_paths[0].as_posix()+'"\nnosort').encode()))
        self.fixture.mutate('source', lambda s: s.update(source_prj_sha256=p.sha_file(file)))
        self.denied()

    def test_missing_nosort_refused(self):
        file = self.fixture.prj
        file.write_bytes(file.read_bytes().replace(b'nosort',b'# no-sort'))
        self.fixture.mutate('source', lambda s: s.update(source_prj_sha256=p.sha_file(file)))
        self.denied()

    def test_source_count_forgery_refused(self):
        self.fixture.mutate('source', lambda s: s['source_hashes'].pop(str(self.fixture.hdl_paths[0])))
        self.denied()

    def test_source_approval_forgery_refused(self):
        self.fixture.mutate('source', lambda s: s.update(full400_authorized=True))
        self.denied()

    def test_release_approval_forgery_refused(self):
        self.fixture.mutate('review', lambda r: r.update(full400_authorized=True))
        self.denied()

    def test_release_link_forgery_refused(self):
        self.fixture.mutate('review', lambda r: r['read_only_evidence_refs']['G0_SOURCE'].update(
            receipt_sha256='f'*64))
        self.denied()

    def test_history_case7_rewrite_refused(self):
        self.fixture.mutate('history', lambda h: h.update(historical_not_reproduced_slots=[3]))
        self.denied()

    def test_adapter_live_results_forgery_refused(self):
        self.fixture.mutate('adapter', lambda a: a.update(full400_executed=True))
        self.denied()

    def test_adapter_one_ordinal_changed_refused(self):
        self.fixture.mutate('adapter', lambda a: a['invocations'][3].update(scenario_id='FI_P400_9999'))
        self.denied()

    def test_adapter_audit_counter_forgery_refused(self):
        self.fixture.mutate('adapter_audit',lambda a: a.update(mapped_contracts_verified=399))
        self.denied()

    def test_v4f4f_pinmap_extra_fake_refused(self):
        self.fixture.mutate('plan',lambda x: x['input_pins'].update(fake='/missing/abc'))
        self.denied()

    def test_dup_json_key_rejected(self):
        path = self.fixture.file('source')
        path.write_bytes(b'{"schema":1,"schema":2}\n')
        self.denied()

    def test_atomic_no_overwrite(self):
        path = self.fixture.repo / 'x.json'
        p.write_new(path,b'one')
        with self.assertRaises(ValueError): p.write_new(path,b'two')
        self.assertEqual(path.read_bytes(),b'one')

    def test_atomic_orphan_tmp_refused(self):
        path = self.fixture.repo / 'x.json'
        save(path.with_name('x.json.tmp'), b'orph')
        with self.assertRaises(ValueError): p.write_new(path,b'not_written')
        self.assertFalse(path.exists())

    def test_execute_denied_before_io(self):
        for mode in ('build','execute','full400'):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError,'DENIED BEFORE IO'):
                p.check_mode(mode, False, False)

    def test_approve_denied_before_io(self):
        for x,y in ((True,False),(False,True)):
            with self.assertRaises(ValueError): p.check_mode('plan',x,y)

    def test_reject_outside_namespace(self):
        with self.assertRaises(ValueError):
            p.dir_arg(self.fixture.repo,Path('../source'), 'v4f4f_', existing=True)

    def test_refuse_existing_output(self):
        out = self.fixture.results / 'v4i_source_snapshot_01'
        out.mkdir()
        with self.assertRaises(ValueError):
            p.dir_arg(self.fixture.repo,out, 'v4i_', existing=False)

    def test_duplicate_receipt_key_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d)/'r.json'
            f.write_bytes(b'{"a":1,"a":2}')
            with self.assertRaises(ValueError): p.read_object(f)

    def test_audit_changed_manifest_field_refused(self):
        m,r = self.fixture.audit_objects()
        m2 = copy.deepcopy(m)
        m2['source_count'] = 400
        with self.assertRaises(ValueError):
            a.audit_model(m2,r,p.sha(p.canonical(m2)),self.fixture.collect())

    def test_audit_forged_receipt_refused(self):
        m,r = self.fixture.audit_objects()
        r2 = dict(r,full400_authorized=True)
        with self.assertRaises(ValueError):
            a.audit_model(m,r2,p.sha(p.canonical(m)),self.fixture.collect())

    def test_audit_crlf_refused(self):
        m,r = self.fixture.audit_objects()
        self.assertNotEqual(p.canonical(m),p.canonical(m).replace(b'\n',b'\r\n'))

    def test_upstream_receipts_unchanged_by_read(self):
        before = {k: p.sha_file(self.fixture.file(k)) for k in p.FILES}
        self.fixture.collect()
        after = {k: p.sha_file(self.fixture.file(k)) for k in p.FILES}
        self.assertEqual(before,after)

    def test_output_namespace_happy(self):
        out = self.fixture.results / 'v4i_source_snapshot_01'
        self.assertEqual(p.dir_arg(self.fixture.repo,out,'v4i_',existing=False),out.resolve())

    def test_output_namespace_noncanonical_repo_anchor(self):
        # Lexically different but canonically identical repo root.  The
        # checked parent must be canonicalized without accepting escape.
        bridge = self.fixture.repo.parent / 'safe_lexical_alias'
        bridge.mkdir()
        alias = bridge / '..' / self.fixture.repo.name
        out = self.fixture.results / 'v4i_source_snapshot_01'
        self.assertEqual(p.dir_arg(alias,out,'v4i_',existing=False),out.resolve())

    def test_output_namespace_sibling_repo_denied(self):
        # Normalizing the anchor must not allow writing to a sibling repo.
        sibling = self.fixture.repo.parent / 'another_repo'
        (sibling / 'fi/results').mkdir(parents=True)
        out = self.fixture.results / 'v4i_source_snapshot_01'
        with self.assertRaises(ValueError):
            p.dir_arg(sibling,out,'v4i_',existing=False)

    def test_source_identity_deterministic(self):
        self.assertEqual(self.fixture.collect()['source_identity_sha256'],
                         self.fixture.collect()['source_identity_sha256'])

    def test_full_replay_writes_only_new_audit(self):
        manifest, receipt = self.fixture.audit_objects()
        inputdir = self.fixture.results / 'v4i_source_snapshot_01'
        inputdir.mkdir()
        save_json(inputdir/'v4i_source_inventory.json',manifest)
        save_json(inputdir/'v4i_source_inventory_receipt.json',receipt)
        args = Namespace(mode='audit',execute=False,approve=False,repo=self.fixture.repo,
                         project=self.fixture.project,vivado=self.fixture.vivado,prj=self.fixture.prj,
                         inventory_dir=inputdir,
                         out_dir=self.fixture.results / 'v4i_source_snapshot_audit_01',
                         **{key+'_dir': self.fixture.evidence[key] for key in
                            ('source','review','history','rehearsal','plan','adapter','adapter_audit')})
        with patch.object(p,'FROZEN',self.fixture.frozen):
            result = a.run_audit(args)
        self.assertEqual(result['compiled_sources_rehashed'],28)
        self.assertTrue((self.fixture.results/'v4i_source_snapshot_audit_01/v4i_source_inventory_audit.json').is_file())


if __name__ == '__main__':
    unittest.main()
