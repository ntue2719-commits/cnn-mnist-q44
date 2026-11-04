#!/usr/bin/env python3
"""V4J offline, source-only content-addressed capture. NO BUILD, NO EXECUTION.

Captures byte copies of already pinned Vivado-authoritative HDL and V4f4F
inputs, while preserving existing evidence as immutable parents. This is NOT
a compiled/elaborated Vivado snapshot, a runnable relocated .prj, or G6 release.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import production_source_snapshot_v4i as v4i
import audit_source_snapshot_v4i as v4i_audit

SCHEMA = 'CNN_PEP_V4J_OFFLINE_SOURCE_BUNDLE_V1'
RECEIPT_SCHEMA = 'CNN_PEP_V4J_OFFLINE_SOURCE_BUNDLE_RECEIPT_V1'
STOP = 'FULL400_BLOCKED_NO_BUILD_NO_XSIM_NO_RELEASE'
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_INPUT_BYTES = 2 * 1024 * 1024 * 1024
SOURCE_FILES = ('v4i_source_inventory.json', 'v4i_source_inventory_receipt.json')
AUDIT_FILES = ('v4i_source_inventory_audit.json',)
BUNDLE_FILES = ('v4j_source_bundle_manifest.json','v4j_source_bundle_receipt.json')
HEX = re.compile(r'[0-9a-f]{64}\Z')


def check(ok: bool, why: str) -> None:
    if not ok:
        raise ValueError(why)


def no_file_link(path: Path) -> None:
    v4i.no_link_chain(path.absolute())
    check(path.is_file() and not path.is_symlink(), 'Missing/symlinked source: ' + str(path))


def exact_json(path: Path) -> dict[str, Any]:
    no_file_link(path)
    data = path.read_bytes()
    obj = v4i.read_object(path)
    check(data == v4i.canonical(obj), 'Noncanonical UTF-8 LF JSON: ' + str(path))
    return obj


def read_pinned_historical_v4f4f_plan(path: Path, expected_raw_sha256: str) -> dict[str, Any]:
    """Read a pinned V4f4F *historical* plan, preserving its raw Windows bytes.

    V4f4F used a text-mode writer on Windows (CRLF), while newly produced
    V4I/V4J audit records MUST remain canonical UTF-8/LF. A legacy plan is
    accepted only when its exact raw SHA is already recorded by V4I and its
    entire byte sequence is canonical JSON with *all* LF replaced by CRLF.
    Mixed endings, different formatting, BOM, duplicate keys and stale SHA
    stay blocked. The original plan is never rewritten or normalized on disk.
    """
    no_file_link(path)
    check(path.name == v4i.FILES['plan'] and
          path.parent.name.startswith('v4f4f_'),
          'Legacy line ending exception is restricted to V4f4F plan only')
    check(type(expected_raw_sha256) is str and
          HEX.fullmatch(expected_raw_sha256) is not None,
          'V4f4F plan SHA256 pin malformed')
    raw = path.read_bytes()
    check(v4i.sha(raw) == expected_raw_sha256,
          'V4f4F historical plan raw SHA256 drift')
    obj = v4i.read_object(path)  # Reject duplicate JSON keys and nonfinite values.
    canonical = v4i.canonical(obj)
    if raw != canonical:
        # Restrict legacy support to pure CRLF of the exact canonical payload;
        # never tolerate mixed LF/CRLF or arbitrary JSON whitespace changes.
        check(b'\r\n' in raw and
              raw.count(b'\r\n') == raw.count(b'\n') == raw.count(b'\r') and
              raw.replace(b'\r\n', b'\n') == canonical,
              'V4f4F historical plan is neither canonical LF nor pure canonical CRLF')
    return obj


def proof_context(repo: Path, project: Path, vivado: Path, prj: Path,
                  evidence: dict[str, Path], inventory_dir: Path,
                  inventory_audit_dir: Path,
                  frozen: dict[str, str] | None = None) -> dict[str, Any]:
    """Rehash originals before a copy. The V4I parents must match byte-for-byte."""
    if frozen is None:
        frozen = v4i.FROZEN
    for raw in (repo, project, vivado, prj, inventory_dir, inventory_audit_dir):
        v4i.no_link_chain(raw.absolute())
    repo = repo.resolve()
    v4i.check(inventory_dir.is_dir() and inventory_audit_dir.is_dir(),
              'Missing V4I inventory or audit directory')
    v4i.check({p.name for p in inventory_dir.iterdir()} == set(SOURCE_FILES) and
              {p.name for p in inventory_audit_dir.iterdir()} == set(AUDIT_FILES),
              'Unexpected V4I evidence files; no silent orphans')
    manifest_file = inventory_dir / SOURCE_FILES[0]
    receipt_file = inventory_dir / SOURCE_FILES[1]
    audit_file = inventory_audit_dir / AUDIT_FILES[0]
    manifest = exact_json(manifest_file)
    receipt = exact_json(receipt_file)
    audit = exact_json(audit_file)
    live = v4i.collect(repo, project, vivado, prj, evidence, frozen=frozen)
    v4i_audit.audit_model(manifest, receipt, v4i.sha_file(manifest_file), live)
    check(audit.get('schema') == v4i_audit.AUDIT_SCHEMA and
          audit.get('source_inventory_sha256') == v4i.sha_file(manifest_file) and
          audit.get('source_inventory_receipt_sha256') == v4i.sha_file(receipt_file) and
          audit.get('source_identity_sha256') == manifest['source_identity_sha256'] and
          audit.get('compiled_sources_rehashed') == manifest['source_count'] and
          audit.get('v4f4f_asset_and_source_pins_rehashed') == manifest['v4f4f_input_pin_count'] and
          audit.get('frozen_rehashed') == 4 and
          audit.get('toolchain_files_rehashed') == len(manifest['toolchain_sha256']) and
          audit.get('independently_qualified_g6') is False and
          audit.get('read_only_rehash_replay') is True and
          audit.get('build_once_executed') is False and
          audit.get('new_xsim_runs') == 0 and
          audit.get('full400_authorized') is False and
          audit.get('release_gate') == v4i.STOP,
          'V4I read-only audit is not linked to exact approved scope')
    plan = read_pinned_historical_v4f4f_plan(
        evidence['plan'] / v4i.FILES['plan'], manifest['v4f4f_plan_sha256'])
    check(plan.get('planned_count') == 400 and
          plan.get('input_fingerprint') == manifest['v4f4f_input_fingerprint'] and
          plan.get('full400_authorized') is False,
          'V4f4F frozen input plan changed')
    pins = plan.get('input_pins')
    check(type(pins) is dict and len(pins) == manifest['v4f4f_input_pin_count'] and
          v4i.sha(v4i.canonical(pins)) == manifest['v4f4f_input_fingerprint'],
          'V4f4F input pins drift')
    return {'repo':repo, 'manifest':manifest, 'receipt':receipt, 'audit':audit,
            'pins':pins, 'inventory_sha':v4i.sha_file(manifest_file),
            'receipt_sha':v4i.sha_file(receipt_file),
            'audit_sha':v4i.sha_file(audit_file)}


def source_records(context: dict[str, Any], *, frozen: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Immutable expected file-to-blob mapping, no executable commands."""
    if frozen is None:
        frozen = v4i.FROZEN
    repo = context['repo']
    manifest, pins = context['manifest'], context['pins']
    grouped: dict[str, dict[str, Any]] = {}

    def add(path: Path, expected: str, role: str) -> None:
        no_file_link(path)
        path = path.resolve()
        check(type(expected) is str and HEX.fullmatch(expected) is not None,
              'Source SHA malformed')
        check(v4i.sha_file(path) == expected,
              'Source drift at capture: ' + str(path))
        nbytes = path.stat().st_size
        check(0 <= nbytes <= MAX_FILE_BYTES, 'Source file exceeds bounded size: ' + str(path))
        key = os.path.normcase(str(path))
        if key in grouped:
            row = grouped[key]
            check(row['sha256'] == expected and row['bytes'] == nbytes,
                  'Conflicting source pins at same path')
            if role not in row['roles']:
                row['roles'].append(role)
            return
        grouped[key] = {'original_path':str(path), 'sha256':expected,
                        'bytes':nbytes, 'roles':[role],
                        'blob': f'blobs/{expected[:2]}/{expected}'}

    for entry in manifest['compiled_source_order']:
        check(type(entry) is dict and type(entry.get('path')) is str and
              type(entry.get('ordinal')) is int and type(entry.get('kind')) is str,
              'V4I compiled source record malformed')
        file = Path(entry['path'])
        check(file.is_absolute(), 'Compiled source path must be absolute')
        add(file, entry['sha256'], 'COMPILED_' + entry['kind'])
    for key, value in pins.items():
        check(type(key) is str and Path(key).is_absolute(), 'V4f4F input pin not absolute')
        add(Path(key), value, 'FROZEN_PLAN_PIN')
    for key, value in frozen.items():
        add(repo / key, value, 'FROZEN_' + key)
    for prop, role in (('project_path','PROJECT_XPR'),('compile_prj_path','COMPILE_PRJ')):
        file = Path(manifest[prop])
        check(file.is_absolute(), 'Vivado authority path not absolute')
        sha_prop = 'source_project_sha256' if role == 'PROJECT_XPR' else 'source_compile_prj_sha256'
        add(file, manifest[sha_prop], role)
    records = sorted(grouped.values(), key=lambda x:x['original_path'].casefold())
    total = sum(item['bytes'] for item in records)
    check(total <= MAX_INPUT_BYTES, 'Total input snapshot size exceeds bounded limit')
    check(len(records) >= len(manifest['compiled_source_order']) and
          all(HEX.fullmatch(row['sha256']) for row in records),
          'Source bundle scope incomplete')
    for row in records:
        row['roles'].sort()
    return records


def make_manifest(context: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    v4 = context['manifest']
    return {
        'schema': SCHEMA,
        'scope': 'COPY_OF_PINNED_INPUT_BYTES_ONLY_NOT_VIVADO_BUILD_OR_PORTABLE_COMPILE',
        'v4i_source_inventory_sha256': context['inventory_sha'],
        'v4i_source_inventory_receipt_sha256': context['receipt_sha'],
        'v4i_source_inventory_audit_sha256': context['audit_sha'],
        'source_identity_sha256': v4['source_identity_sha256'],
        'v4f4f_input_fingerprint': v4['v4f4f_input_fingerprint'],
        'compiled_source_count': v4['source_count'],
        'v4f4f_pin_count': v4['v4f4f_input_pin_count'],
        'frozen_pin_count': 4,
        'input_record_count': len(records),
        'unique_blob_count': len({row['blob'] for row in records}),
        'input_bytes_recorded': sum(row['bytes'] for row in records),
        'compile_order': [{'ordinal':r['ordinal'], 'path':r['path'], 'sha256':r['sha256'],
                           'kind':r['kind']} for r in v4['compiled_source_order']],
        'compile_prj_rewritten': False,
        'portable_compiled_project_proven': False,
        'vivado_toolchain_copied': False,
        'vivado_toolchain_pins': v4['toolchain_sha256'],
        'source_snapshot_bytes_copied': True,
        'vivado_build_once_executed': False,
        'xelab_executed': False,
        'new_xsim_runs': 0,
        'fi_outcomes_committed': 0,
        'G6_production_qualified': False,
        'G7_scientific_approved': False,
        'full400_authorized': False,
        'full400_executed': False,
        'release_gate': STOP,
        'files': records,
    }


def verify_record_shape(manifest: dict[str, Any]) -> None:
    check(manifest.get('schema') == SCHEMA and
          manifest.get('scope') == 'COPY_OF_PINNED_INPUT_BYTES_ONLY_NOT_VIVADO_BUILD_OR_PORTABLE_COMPILE' and
          manifest.get('compiled_source_count') == 28 and
          manifest.get('v4f4f_pin_count',0) >= 55 and
          manifest.get('frozen_pin_count') == 4 and
          manifest.get('compile_prj_rewritten') is False and
          manifest.get('portable_compiled_project_proven') is False and
          manifest.get('vivado_toolchain_copied') is False and
          manifest.get('source_snapshot_bytes_copied') is True and
          manifest.get('vivado_build_once_executed') is False and
          manifest.get('xelab_executed') is False and
          manifest.get('new_xsim_runs') == 0 and
          manifest.get('fi_outcomes_committed') == 0 and
          manifest.get('G6_production_qualified') is False and
          manifest.get('G7_scientific_approved') is False and
          manifest.get('full400_authorized') is False and
          manifest.get('full400_executed') is False and
          manifest.get('release_gate') == STOP,
          'Source bundle has forbidden execution claims or wrong scope')
    files = manifest.get('files')
    check(type(files) is list and len(files) == manifest.get('input_record_count') and
          type(manifest.get('input_bytes_recorded')) is int and
          all(type(row) is dict for row in files) and
          sum(row.get('bytes', -1) for row in files) == manifest['input_bytes_recorded'],
          'Malformed bundle records or total size')
    all_blobs = set()
    for row in files:
        check(type(row) is dict and set(row) == {'original_path','sha256','bytes','roles','blob'} and
              type(row['original_path']) is str and Path(row['original_path']).is_absolute() and
              type(row['sha256']) is str and HEX.fullmatch(row['sha256']) is not None and
              type(row['bytes']) is int and 0 <= row['bytes'] <= MAX_FILE_BYTES and
              type(row['roles']) is list and row['roles'] == sorted(set(row['roles'])) and
              all(type(r) is str and r for r in row['roles']) and
              row['blob'] == f'blobs/{row["sha256"][:2]}/{row["sha256"]}' and
              not PurePosixPath(row['blob']).is_absolute(),
              'Malformed/unsafe source record or path')
        all_blobs.add(row['blob'])
    check(len(all_blobs) == manifest.get('unique_blob_count') and
          len(files) >= 28 and len(all_blobs) >= 1,
          'Unexpected duplicate/content blob count')


def write_exclusive(path: Path, data: bytes) -> None:
    check(not path.exists() and not path.is_symlink(), 'Output already exists: '+str(path))
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def copy_one(source: Path, dst: Path, expected: str, size: int) -> None:
    no_file_link(source)
    check(not dst.exists(), 'Duplicate bundle blob')
    dst.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    n = 0
    with source.open('rb') as ins, dst.open('xb') as outs:
        for block in iter(lambda: ins.read(1024*1024), b''):
            n += len(block)
            check(n <= MAX_FILE_BYTES, 'Source grew beyond file cap while copying')
            outs.write(block)
            h.update(block)
        outs.flush()
        os.fsync(outs.fileno())
    check(n == size and h.hexdigest() == expected and
          v4i.sha_file(source) == expected and v4i.sha_file(dst) == expected,
          'Source changed during capture, copied blob invalid')


def capture(repo: Path, out: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Create a new immutable-by-policy snapshot. An interrupted stage is preserved."""
    verify_record_shape(manifest)
    repo = repo.resolve()
    out = v4i.dir_arg(repo, out, 'v4j_', existing=False)
    stage = out.with_name('.' + out.name + '.inflight')
    check(not stage.exists() and not stage.is_symlink(),
          'Existing interrupted V4J capture; preserve for forensic review')
    stage.mkdir(parents=False, exist_ok=False)
    sha_written: set[str] = set()
    for row in manifest['files']:
        if row['blob'] in sha_written:
            continue
        copy_one(Path(row['original_path']), stage / row['blob'], row['sha256'], row['bytes'])
        sha_written.add(row['blob'])
    # A final pass catches edits after an earlier blob was copied. It cannot
    # prove future immutability; audits rehash original bytes independently.
    for row in manifest['files']:
        check(v4i.sha_file(Path(row['original_path'])) == row['sha256'],
              'Original input drift during bundle capture: ' + row['original_path'])
    for tool, digest in manifest['vivado_toolchain_pins'].items():
        check(v4i.sha_file(Path(tool)) == digest,
              'Toolchain drift during bundle capture: ' + tool)
    encoded = v4i.canonical(manifest)
    write_exclusive(stage / BUNDLE_FILES[0], encoded)
    receipt = {
        'schema': RECEIPT_SCHEMA,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'bundle_manifest_sha256': v4i.sha(encoded),
        'v4i_source_inventory_sha256': manifest['v4i_source_inventory_sha256'],
        'v4i_source_inventory_audit_sha256': manifest['v4i_source_inventory_audit_sha256'],
        'source_identity_sha256': manifest['source_identity_sha256'],
        'copied_input_records': len(manifest['files']),
        'unique_blobs_rehashed': len(sha_written),
        'vivado_build_once_executed': False,
        'new_xsim_runs': 0,
        'full400_authorized': False,
        'release_gate': STOP,
    }
    write_exclusive(stage / BUNDLE_FILES[1], v4i.canonical(receipt))
    check(not out.exists(), 'Output appeared concurrently during capture')
    stage.rename(out)
    return receipt


def artifact_dir(repo: Path, arg: Path, name_prefix: str, *, existing: bool) -> Path:
    return v4i.dir_arg(repo, arg, name_prefix, existing=existing)


def evidence_dirs(repo: Path, args: argparse.Namespace) -> dict[str, Path]:
    return {k: artifact_dir(repo, getattr(args, k + '_dir'), pre, existing=True)
            for k,pre in (('source','v4f4f_'),('review','v4f4f_'),('history','v4f4f_'),
                          ('rehearsal','v4f4f_'),('plan','v4f4f_'),
                          ('adapter','v4h_exec_'),('adapter_audit','v4h_exec_'))}


def add_context_args(p: argparse.ArgumentParser) -> None:
    p.add_argument('--repo', type=Path, default=Path.cwd())
    p.add_argument('--project', type=Path, required=True)
    p.add_argument('--vivado', type=Path, required=True)
    p.add_argument('--prj', type=Path, default=Path('fi/results/pilot400_v3a_28_01/source_vlog.prj'))
    p.add_argument('--source-dir', type=Path, default=Path('fi/results/v4f4f_source_preflight_01'))
    p.add_argument('--review-dir', type=Path, default=Path('fi/results/v4f4f_release_review_01'))
    p.add_argument('--history-dir', type=Path, default=Path('fi/results/v4f4f_history_audit_01'))
    p.add_argument('--rehearsal-dir', type=Path, default=Path('fi/results/v4f4f_rehearsal_audit_01'))
    p.add_argument('--plan-dir', type=Path, default=Path('fi/results/v4f4f_plan_01'))
    p.add_argument('--adapter-dir', type=Path, default=Path('fi/results/v4h_exec_adapter_plan_01'))
    p.add_argument('--adapter-audit-dir', type=Path, default=Path('fi/results/v4h_exec_adapter_audit_01'))
    p.add_argument('--inventory-dir', type=Path, default=Path('fi/results/v4i_source_snapshot_01'))
    p.add_argument('--inventory-audit-dir', type=Path, default=Path('fi/results/v4i_source_snapshot_audit_01'))
    p.add_argument('--out-dir', type=Path, default=Path('fi/results/v4j_source_bundle_01'))
    p.add_argument('--execute', action='store_true')
    p.add_argument('--approve', action='store_true')


def normalized_path(repo: Path, arg: Path) -> Path:
    raw = arg if arg.is_absolute() else repo / arg
    v4i.no_link_chain(raw.absolute())
    return raw.resolve()


def cli() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('capture','build','execute','full400'), default='capture')
    add_context_args(p)
    args = p.parse_args()
    try:
        check(args.mode == 'capture' and not args.execute and not args.approve,
              'DENIED BEFORE IO: V4J does not build, execute or approve anything')
        v4i.no_link_chain(args.repo.absolute())
        repo = args.repo.resolve()
        out = artifact_dir(repo, args.out_dir, 'v4j_', existing=False)
        inventory = artifact_dir(repo,args.inventory_dir,'v4i_',existing=True)
        inventory_audit = artifact_dir(repo,args.inventory_audit_dir,'v4i_',existing=True)
        e = evidence_dirs(repo,args)
        context = proof_context(repo, normalized_path(repo,args.project),
                                normalized_path(repo,args.vivado),
                                normalized_path(repo,args.prj),e,inventory,inventory_audit)
        manifest = make_manifest(context,source_records(context))
        capture(repo,out,manifest)
        print(f'V4J_SOURCE_BYTES_CAPTURED: {manifest["compiled_source_count"]} compiled entries; '
              f'{manifest["v4f4f_pin_count"]} input pins; '
              f'{manifest["unique_blob_count"]} unique blobs; '
              '0 Vivado build; 0 XSim; FULL400 BLOCKED',flush=True)
        print('V4J_OUTPUT: '+str(out),flush=True)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        raise SystemExit('V4J_CAPTURE_BLOCKED: '+str(exc)) from exc

if __name__ == '__main__':
    cli()
