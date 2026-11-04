#!/usr/bin/env python3
"""V4I: read-only authoritative source inventory. No build, process, or FI code.

The source byte inventory is NOT a Vivado build-once snapshot. It pins sources
now and will fail closed if they drift. This version has no execution interface.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

SCHEMA = 'CNN_PEP_V4I_SOURCE_INVENTORY_CANDIDATE_V1'
RECEIPT_SCHEMA = 'CNN_PEP_V4I_SOURCE_INVENTORY_RECEIPT_V1'
SOURCE_SCHEMA = 'CNN_PEP_V4F4F_VIVADO_READONLY_SOURCE_PREFLIGHT_V1'
RELEASE_SCHEMA = 'CNN_PEP_V4F4F_INDEPENDENT_RELEASE_GAP_V1'
ADAPTER_SCHEMA = 'CNN_PEP_V4H_EXEC_ADAPTER_CONTRACT_CANDIDATE_V1'
ADAPTER_AUDIT_SCHEMA = 'CNN_PEP_V4H_EXEC_INDEPENDENT_OFFLINE_AUDIT_V1'
STOP = 'FULL400_BLOCKED_NO_BUILD_NO_EXECUTION_G6_NOT_QUALIFIED_G7_NOT_APPROVED'
FROZEN = {
    'fi/config/fi_targets_candidates_v2.csv': 'd9014b1140223cfbf58799138cf10b68782d3b67d134df8cc3f329324ddf591d',
    'fi/config/pilot400_targets.csv': '98704ad9e36ded1b2711630f86ac390710c1e92743cb3604d9eab9d1a9474d25',
    'fi/scenarios/pilot400.csv': 'cde77a6e504367192234ebab9cfdbc313a74442223f00b2ad277ca18404510f4',
    'fi/tb/tb_cnn_mnist_fi_pilot400.v': '2a720679a65daaf157c944cbb05b64d606567bd7653811b648f102f1babbbf13',
}
HDL_PATTERN = re.compile(r'"([^"\r\n]+\.(?:v|sv))"', re.IGNORECASE)
HEX = re.compile(r'^[0-9a-f]{64}$')
NEEDED_RTL = {'cnn_mnist_top.v', 'top_fsm.v', 'argmax.v',
              'fully_connected.v', 'feature_mem1.v', 'feature_mem2.v',
              'conv1_layer.v', 'conv2_layer.v'}
TOOL_NAMES = ('xvlog.bat', 'xelab.bat', 'xsim.bat')
FILES = {
    'source': 'v4f4f_source_preflight.json',
    'review': 'v4f4f_release_gaps.json',
    'history': 'v4f4f_historical_audit.json',
    'rehearsal': 'v4f4f_rehearsal_audit.json',
    'adapter': 'v4h_exec_adapter_400.json',
    'adapter_receipt': 'v4h_exec_receipt.json',
    'adapter_audit': 'v4h_exec_independent_audit.json',
    'plan': 'v4f4f_production_contract.json',
}


def check(ok: bool, why: str) -> None:
    if not ok:
        raise ValueError(why)


def canonical(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False,
                       allow_nan=False) + '\n').encode('utf-8')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    check(path.is_file() and not path.is_symlink() and path.stat().st_size < 24_000_000,
          'Missing/symlinked/oversize input: ' + str(path))
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in pairs:
            check(k not in out, 'Duplicate JSON key: ' + k)
            out[k] = v
        return out
    obj = json.loads(path.read_bytes().decode('utf-8'), object_pairs_hook=unique,
                     parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    check(type(obj) is dict, 'JSON must be an object')
    return obj


def check_mode(mode: str, execute: bool, approve: bool) -> None:
    check(mode == 'plan' and not execute and not approve,
          'V4I DENIED BEFORE IO: build/execute/full400/approval not implemented')


def no_link_chain(path: Path) -> None:
    for p in (path, *path.parents):
        check(not p.is_symlink(), 'Symlink component forbidden: ' + str(p))


def dir_arg(repo: Path, arg: Path, prefix: str, *, existing: bool) -> Path:
    # Canonicalize the repository anchor before comparing it with the
    # resolved candidate.  Windows temp directories can have equivalent
    # lexical and canonical spellings (e.g., 8.3 names).  Preserve the
    # original symlink-chain refusal before following either path.
    no_link_chain(repo.absolute())
    repo = repo.resolve()
    src = arg if arg.is_absolute() else repo / arg
    no_link_chain(src.absolute())
    p = src.resolve()
    check(p.parent == repo / 'fi/results' and
          re.fullmatch(prefix + r'[A-Za-z0-9_]+', p.name) is not None,
          'Directory outside approved fi/results namespace')
    check(p.is_dir() if existing else not p.exists(),
          'Evidence directory missing or already exists: ' + str(p))
    return p


def source_entries(repo: Path, project: Path, prj: Path, source: dict[str, Any],
                   *, frozen: dict[str, str]) -> list[dict[str, Any]]:
    check(source.get('schema') == SOURCE_SCHEMA and
          source.get('source_preflight') == 'READONLY_VERIFIED_NO_COMPILE_ELAB_XSIM' and
          source.get('source_authority') == 'VIVADO_XPR_PINNED_V3A_PRJ' and
          source.get('built_snapshot') is False and
          source.get('new_xsim_runs') == 0 and
          source.get('full400_authorized') is False and
          source.get('G6_production') == 'NOT_QUALIFIED' and
          source.get('G7') == 'PREPARED_NOT_APPROVED',
          'Source receipt lacks fail-closed V4f4F preflight semantics')
    check(project.is_file() and not project.is_symlink() and project.suffix.lower() == '.xpr' and
          sha_file(project) == source.get('project_sha256'), 'Project drift from source receipt')
    check(prj.is_file() and not prj.is_symlink() and
          sha_file(prj) == source.get('source_prj_sha256'), 'Compile .prj drift from source receipt')
    txt = prj.read_text(encoding='utf-8', errors='replace')
    check(re.search(r'(?m)^nosort\s*$', txt) is not None,
          'Compile order cannot be pinned: no nosort directive')
    listed = HDL_PATTERN.findall(txt)
    check(len(listed) >= 22 and len(listed) == len(set(x.casefold() for x in listed)),
          'Missing/duplicated HDL sources in compile .prj')
    known = source.get('source_hashes')
    check(type(known) is dict and len(known) == len(listed),
          'Source receipt/compile source count mismatch')
    srcroot = (project.parent / (project.stem + '.srcs')).resolve()
    names: set[str] = set()
    entries: list[dict[str, Any]] = []
    for index, name in enumerate(listed, 1):
        original_path = Path(name.replace('\\', '/'))
        no_link_chain(original_path.absolute())
        file = original_path.resolve()
        check(file.is_file() and not file.is_symlink(), 'Compiled HDL missing/symlinked: ' + name)
        expected = known.get(str(file))
        check(type(expected) is str and HEX.fullmatch(expected) is not None and
              sha_file(file) == expected, 'Compiled HDL modified or source pin missing: ' + name)
        base = file.name.lower()
        check(base not in names, 'Duplicate RTL basename: ' + base)
        names.add(base)
        if base == 'glbl.v':
            kind = 'GENERATED_GLBL'
            check(file.is_relative_to(project.parent.resolve()), 'Generated glbl outside project root')
        elif base == 'tb_cnn_mnist_fi_pilot400.v':
            kind = 'FROZEN_FI_TB'
            check(expected == frozen['fi/tb/tb_cnn_mnist_fi_pilot400.v'], 'Original FI TB changed')
        else:
            kind = 'VIVADO_AUTHORITY_RTL'
            check(file.is_relative_to(srcroot), 'Compiled RTL not under selected Vivado .srcs')
        entries.append({'ordinal': index, 'path': str(file), 'basename': file.name,
                        'kind': kind, 'sha256': expected, 'bytes': file.stat().st_size})
    check(len(entries) >= 22 and {'glbl.v','tb_cnn_mnist_fi_pilot400.v'} <= names and
          NEEDED_RTL <= names and
          len([e for e in entries if e['kind'] == 'VIVADO_AUTHORITY_RTL']) >= 20,
          'Incomplete Vivado-source/TB/glbl compile order')
    return entries


def collect(repo: Path, project: Path, vivado: Path, prj: Path,
            evidence: dict[str, Path], *, frozen: dict[str, str] | None = None) -> dict[str, Any]:
    """Read all authoritative bytes. No file in the project is written."""
    if frozen is None:
        frozen = FROZEN
    check(repo.is_dir() and (repo / 'fi/results').is_dir(), 'Missing repository/results')
    expected_paths = {k: evidence['adapter' if k == 'adapter_receipt' else k] / FILES[k]
                      for k in FILES}
    obj = {k: read_object(p) for k,p in expected_paths.items()}
    dig = {k: sha_file(p) for k,p in expected_paths.items()}
    s, r, h, rehearse, a, ar, aa, plan = (
        obj['source'], obj['review'], obj['history'], obj['rehearsal'],
        obj['adapter'], obj['adapter_receipt'], obj['adapter_audit'], obj['plan'])
    check(r.get('schema') == RELEASE_SCHEMA and
          r.get('G0_G7_signed_authorization') is False and
          r.get('full400_runner_qualified') is False and
          r.get('full400_authorized') is False and
          r.get('full400_executed') is False and
          r.get('new_xsim_runs_during_audit') == 0,
          'Release receipt is not blocked or has unexpected schema')
    refs = r.get('read_only_evidence_refs')
    check(type(refs) is dict, 'Missing release receipt references')
    for label, key in (('G0_SOURCE','source'), ('G6_BOUNDED_HISTORY','history'),
                       ('G6_400_SYNTHETIC','rehearsal')):
        item = refs.get(label)
        check(type(item) is dict and item.get('status') == 'RECEIPT_PRESENT_NOT_RELEASE_AUTHORIZATION' and
              item.get('receipt_sha256') == dig[key],
              'Release-parent SHA256 mismatch: ' + label)
    check(h.get('attempt_slots_rechecked') == 8 and
          h.get('raw_artifacts_sha256_rechecked') == 590 and
          h.get('bounded_valid_fi_results') == 2 and
          h.get('historical_not_reproduced_slots') == [3, 7] and
          h.get('full400_authorized') is False,
          'History receipt not exact closed eight-attempt scope')
    check(rehearse.get('synthetic_events_rehashed') == 400 and
          rehearse.get('full400_authorized') is False and
          rehearse.get('G6_production') == 'NOT_QUALIFIED',
          'Rehearsal receipt not 400 synthetic events')
    check(a.get('schema') == ADAPTER_SCHEMA and a.get('frozen_scenarios') == 400 and
          a.get('batch_count') == 16 and a.get('batch_size') == 25 and
          a.get('source_snapshot_built') is False and
          a.get('new_xsim_runs') == 0 and
          a.get('G6_production_qualified') is False and
          a.get('G7_scientific_approved') is False and
          a.get('full400_authorized') is False and
          a.get('full400_executed') is False and
          type(a.get('invocations')) is list and len(a['invocations']) == 400,
          'V4H adapter was forged or incomplete')
    check(all(type(item) is dict and
              item.get('scenario_id') == f'FI_P400_{i:04d}' and
              item.get('ready_to_execute') is False and
              item.get('checkpoint_committed') is False and
              item.get('design_outcome_observed') is False
              for i, item in enumerate(a['invocations'], 1)),
          'V4H adapter scenario/attempt state changed')
    check(ar.get('adapter_plan_sha256') == dig['adapter'] and
          ar.get('full400_authorized') is False and
          ar.get('new_xsim_runs') == 0,
          'V4H adapter receipt linkage mismatch')
    check(aa.get('schema') == ADAPTER_AUDIT_SCHEMA and
          aa.get('adapter_sha256') == dig['adapter'] and
          aa.get('adapter_receipt_sha256') == dig['adapter_receipt'] and
          aa.get('mapped_contracts_verified') == 400 and
          aa.get('future_artifact_paths_verified') == 3200 and
          aa.get('new_xsim_runs') == 0 and
          aa.get('build_once_executed') is False and
          aa.get('G6_production_qualified') is False and
          aa.get('full400_authorized') is False,
          'V4H independent audit linkage mismatch')
    check(plan.get('planned_count') == 400 and
          plan.get('input_fingerprint') == a.get('source_input_fingerprint') and
          plan.get('full400_authorized') is False,
          'V4f4F frozen plan not linked to adapter')
    pins = plan.get('input_pins')
    check(type(pins) is dict and len(pins) >= 55 and
          sha(canonical(pins)) == plan['input_fingerprint'],
          'Malformed V4f4F pins/fingerprint')
    for key, value in pins.items():
        file = Path(key)
        no_link_chain(file.absolute())
        check(type(key) is str and type(value) is str and HEX.fullmatch(value) is not None and
              file.is_file() and not file.is_symlink() and sha_file(file) == value,
              'V4f4F source/asset pin drift: ' + str(key))
    for rel, expected in frozen.items():
        check((repo / rel).is_file() and not (repo / rel).is_symlink() and
              sha_file(repo / rel) == expected,
              'Frozen SHA256 mismatch: ' + rel)
    check(vivado.is_file() and not vivado.is_symlink() and
          vivado.name.lower() in ('vivado.bat', 'vivado.exe'), 'Vivado path invalid')
    binaries = [vivado] + [vivado.parent / name for name in TOOL_NAMES]
    recorded = s.get('toolchain_sha256')
    check(type(recorded) is dict and set(recorded) == {str(p.resolve()) for p in binaries},
          'Vivado/XSim toolchain references do not match selected path')
    for tool in binaries:
        check(tool.is_file() and not tool.is_symlink() and
              sha_file(tool) == recorded[str(tool.resolve())],
              'Live toolchain byte drift: ' + str(tool))
    entries = source_entries(repo, project, prj, s, frozen=frozen)
    source_identity = {
        'project_sha256': s['project_sha256'],
        'compile_prj_sha256': s['source_prj_sha256'],
        'compiled_source_order': entries,
        'toolchain_sha256': dict(sorted(recorded.items())),
    }
    return {
        'schema': SCHEMA,
        'scope': 'SOURCE_BYTE_INVENTORY_ONLY_NOT_A_COMPILED_SNAPSHOT',
        'source_authority': 'VIVADO_XPR_PINNED_V3A_PRJ',
        'source_preflight_receipt_sha256': dig['source'],
        'release_gap_receipt_sha256': dig['review'],
        'history_receipt_sha256': dig['history'],
        'rehearsal_receipt_sha256': dig['rehearsal'],
        'adapter_plan_sha256': dig['adapter'],
        'adapter_receipt_sha256': dig['adapter_receipt'],
        'adapter_independent_audit_sha256': dig['adapter_audit'],
        'v4f4f_plan_sha256': dig['plan'],
        'v4f4f_input_fingerprint': plan['input_fingerprint'],
        'v4f4f_input_pin_count': len(pins),
        'frozen_inputs_sha256': dict(sorted(frozen.items())),
        'project_path': str(project.resolve()),
        'compile_prj_path': str(prj.resolve()),
        'source_project_sha256': s['project_sha256'],
        'source_compile_prj_sha256': s['source_prj_sha256'],
        'compiled_source_order': entries,
        'source_count': len(entries),
        'rtl_count': sum(e['kind'] == 'VIVADO_AUTHORITY_RTL' for e in entries),
        'toolchain_sha256': dict(sorted(recorded.items())),
        'source_identity_sha256': sha(canonical(source_identity)),
        'source_inventory_pinned_now': True,
        'source_snapshot_materialized': False,
        'compiled_build_snapshot_exists': False,
        'build_once_executed': False,
        'windows_job_object_exercised': False,
        'xsim_runs': 0,
        'fi_results_committed': 0,
        'G6_production_qualified': False,
        'G7_scientific_approved': False,
        'full400_authorized': False,
        'full400_executed': False,
        'release_gate': STOP,
    }


def write_new(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + '.tmp')
    check(not path.exists() and not tmp.exists() and
          not path.is_symlink() and not tmp.is_symlink(),
          'Existing/orphan output file')
    with tmp.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    check(not path.exists(), 'Destination appeared mid-write')
    tmp.replace(path)


def cli() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('plan','build','execute','full400'), default='plan')
    p.add_argument('--repo', type=Path, default=Path.cwd())
    p.add_argument('--source-dir', type=Path, default=Path('fi/results/v4f4f_source_preflight_01'))
    p.add_argument('--review-dir', type=Path, default=Path('fi/results/v4f4f_release_review_01'))
    p.add_argument('--history-dir', type=Path, default=Path('fi/results/v4f4f_history_audit_01'))
    p.add_argument('--rehearsal-dir', type=Path, default=Path('fi/results/v4f4f_rehearsal_audit_01'))
    p.add_argument('--plan-dir', type=Path, default=Path('fi/results/v4f4f_plan_01'))
    p.add_argument('--adapter-dir', type=Path, default=Path('fi/results/v4h_exec_adapter_plan_01'))
    p.add_argument('--adapter-audit-dir', type=Path, default=Path('fi/results/v4h_exec_adapter_audit_01'))
    p.add_argument('--project', type=Path, required=True)
    p.add_argument('--vivado', type=Path, required=True)
    p.add_argument('--prj', type=Path, default=Path('fi/results/pilot400_v3a_28_01/source_vlog.prj'))
    p.add_argument('--out-dir', type=Path, default=Path('fi/results/v4i_source_snapshot_01'))
    p.add_argument('--execute', action='store_true')
    p.add_argument('--approve', action='store_true')
    args = p.parse_args()
    try:
        check_mode(args.mode, args.execute, args.approve)
        no_link_chain(args.repo.absolute())
        repo = args.repo.resolve()
        e = {k: dir_arg(repo, getattr(args, k + '_dir'), prefix, existing=True)
             for k,prefix in (('source','v4f4f_'),('review','v4f4f_'),('history','v4f4f_'),
                              ('rehearsal','v4f4f_'),('plan','v4f4f_'),
                              ('adapter','v4h_exec_'),('adapter_audit','v4h_exec_'))}
        out = dir_arg(repo, args.out_dir, 'v4i_', existing=False)
        raw_project = args.project if args.project.is_absolute() else repo / args.project
        raw_vivado = args.vivado if args.vivado.is_absolute() else repo / args.vivado
        raw_prj = args.prj if args.prj.is_absolute() else repo / args.prj
        for raw in (raw_project, raw_vivado, raw_prj):
            no_link_chain(raw.absolute())
        project, vivado, prj = raw_project.resolve(), raw_vivado.resolve(), raw_prj.resolve()
        manifest = collect(repo, project, vivado, prj, e)
        out.mkdir(parents=False, exist_ok=False)
        file = out / 'v4i_source_inventory.json'
        write_new(file, canonical(manifest))
        receipt = {
            'schema': RECEIPT_SCHEMA,
            'created_utc': datetime.now(timezone.utc).isoformat(),
            'source_inventory_sha256': sha_file(file),
            'source_identity_sha256': manifest['source_identity_sha256'],
            'source_preflight_receipt_sha256': manifest['source_preflight_receipt_sha256'],
            'adapter_independent_audit_sha256': manifest['adapter_independent_audit_sha256'],
            'source_snapshot_materialized': False,
            'build_once_executed': False,
            'new_xsim_runs': 0,
            'full400_authorized': False,
            'release_gate': STOP,
        }
        write_new(out / 'v4i_source_inventory_receipt.json', canonical(receipt))
        print(f'V4I_SOURCE_INVENTORY_PASS: {manifest["source_count"]} compiled source entries; '
              f'{manifest["v4f4f_input_pin_count"]} V4f4F pins rehashed; 4/4 frozen; '
              '0 build; 0 XSim; FULL400 BLOCKED', flush=True)
        print('V4I_OUTPUT: ' + str(out), flush=True)
    except (ValueError, OSError, RuntimeError, TypeError, KeyError) as exc:
        raise SystemExit('V4I_SOURCE_INVENTORY_BLOCKED: ' + str(exc)) from exc

if __name__ == '__main__':
    cli()
