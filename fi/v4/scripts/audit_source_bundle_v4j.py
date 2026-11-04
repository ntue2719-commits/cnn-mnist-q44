#!/usr/bin/env python3
"""V4J offline second-pass content-addressed source bundle verification.

Does not build/run Vivado/XSim, does not promote G6 or authorize FI.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0,str(HERE))
import production_source_bundle_v4j as j
import production_source_snapshot_v4i as v4i

AUDIT_SCHEMA = 'CNN_PEP_V4J_SOURCE_BUNDLE_REHASH_AUDIT_V1'


def verify_bundle(bundle: Path, expected: dict[str, Any], *, live_bytes: bool=True) -> tuple[dict[str, Any], dict[str, Any]]:
    v4i.no_link_chain(bundle.absolute())
    j.check(bundle.is_dir() and not bundle.is_symlink(), 'Bundle directory missing or symlinked')
    mpath = bundle / j.BUNDLE_FILES[0]
    rpath = bundle / j.BUNDLE_FILES[1]
    manifest = j.exact_json(mpath)
    receipt = j.exact_json(rpath)
    j.verify_record_shape(manifest)
    j.check(manifest == expected, 'Bundle does not match fresh V4I parent/live pins')
    j.check(set(receipt) == {'schema','created_utc','bundle_manifest_sha256',
                            'v4i_source_inventory_sha256','v4i_source_inventory_audit_sha256',
                            'source_identity_sha256','copied_input_records','unique_blobs_rehashed',
                            'vivado_build_once_executed','new_xsim_runs','full400_authorized','release_gate'} and
            receipt['schema'] == j.RECEIPT_SCHEMA and
            receipt['bundle_manifest_sha256'] == v4i.sha_file(mpath) and
            receipt['v4i_source_inventory_sha256'] == manifest['v4i_source_inventory_sha256'] and
            receipt['v4i_source_inventory_audit_sha256'] == manifest['v4i_source_inventory_audit_sha256'] and
            receipt['source_identity_sha256'] == manifest['source_identity_sha256'] and
            receipt['copied_input_records'] == manifest['input_record_count'] and
            receipt['unique_blobs_rehashed'] == manifest['unique_blob_count'] and
            receipt['vivado_build_once_executed'] is False and
            receipt['new_xsim_runs'] == 0 and
            receipt['full400_authorized'] is False and
            receipt['release_gate'] == j.STOP,
            'Invalid or forged bundle receipt')
    expected_blobs = {r['blob']: (r['sha256'],r['bytes']) for r in manifest['files']}
    actual = set()
    for path in bundle.rglob('*'):
        v4i.no_link_chain(path.absolute())
        rel = path.relative_to(bundle).as_posix()
        if path.is_file():
            actual.add(rel)
            j.check(path.is_file() and not path.is_symlink(), 'Invalid source bundle artifact')
        elif path.is_dir():
            # No arbitrary directories beyond content-addressed blob hierarchy.
            j.check(rel == 'blobs' or (rel.startswith('blobs/') and len(rel) == 8 and
                                       all(c in '0123456789abcdef' for c in rel[6:])),
                    'Unexpected/orphan bundle directory: '+rel)
        else:
            raise ValueError('Unsupported bundle inode')
    j.check(actual == set(expected_blobs) | set(j.BUNDLE_FILES),
            'Orphan/missing bundle blob or receipt detected')
    for rel,(digest,size) in expected_blobs.items():
        path = bundle / rel
        j.check(path.stat().st_size == size and v4i.sha_file(path) == digest,
                'Bundle byte copy corrupted or wrong size: '+rel)
    if live_bytes:
        for r in manifest['files']:
            path = Path(r['original_path'])
            j.no_file_link(path)
            j.check(v4i.sha_file(path) == r['sha256'],
                    'Live authoritative input drift since capture: '+str(path))
    return manifest,receipt


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    j.check(args.mode == 'audit' and not args.execute and not args.approve,
            'DENIED BEFORE IO: no compilation, execution or approval')
    v4i.no_link_chain(args.repo.absolute())
    repo = args.repo.resolve()
    bundle = j.artifact_dir(repo,args.bundle_dir,'v4j_',existing=True)
    out = j.artifact_dir(repo,args.out_dir,'v4j_',existing=False)
    inventory = j.artifact_dir(repo,args.inventory_dir,'v4i_',existing=True)
    inventory_audit = j.artifact_dir(repo,args.inventory_audit_dir,'v4i_',existing=True)
    evidence = j.evidence_dirs(repo,args)
    ctx = j.proof_context(repo,j.normalized_path(repo,args.project),
                          j.normalized_path(repo,args.vivado),
                          j.normalized_path(repo,args.prj),evidence,inventory,inventory_audit)
    expect = j.make_manifest(ctx,j.source_records(ctx))
    manifest,receipt = verify_bundle(bundle,expect)
    res = {
        'schema':AUDIT_SCHEMA,
        'created_utc':datetime.now(timezone.utc).isoformat(),
        'bundle_manifest_sha256':v4i.sha_file(bundle / j.BUNDLE_FILES[0]),
        'bundle_receipt_sha256':v4i.sha_file(bundle / j.BUNDLE_FILES[1]),
        'v4i_source_inventory_sha256':ctx['inventory_sha'],
        'v4i_source_inventory_audit_sha256':ctx['audit_sha'],
        'compiled_sources_rehashed':manifest['compiled_source_count'],
        'input_pins_rehashed':manifest['v4f4f_pin_count'],
        'input_records_rehashed':manifest['input_record_count'],
        'content_blobs_rehashed':manifest['unique_blob_count'],
        'source_bytes_copied_and_replayed':True,
        'compiled_snapshot_exists':False,
        'vivado_build_once_executed':False,
        'new_xsim_runs':0,
        'fi_outcomes_committed':0,
        'G6_production_qualified':False,
        'G7_scientific_approved':False,
        'full400_authorized':False,
        'release_gate':j.STOP,
    }
    out.mkdir(parents=False,exist_ok=False)
    j.write_exclusive(out/'v4j_source_bundle_audit.json',v4i.canonical(res))
    print(f'V4J_SOURCE_BUNDLE_AUDIT_PASS: {res["compiled_sources_rehashed"]} compiled entries; '
          f'{res["input_pins_rehashed"]} input pins; '
          f'{res["content_blobs_rehashed"]} blob hashes; 0 build; 0 XSim; '
          'G6 NOT QUALIFIED; FULL400 BLOCKED',flush=True)
    print('V4J_AUDIT_OUTPUT: '+str(out),flush=True)
    return res


def cli() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('audit','build','execute','full400'),default='audit')
    j.add_context_args(p)
    p.add_argument('--bundle-dir',type=Path,default=Path('fi/results/v4j_source_bundle_01'))
    p.set_defaults(out_dir=Path('fi/results/v4j_source_bundle_audit_01'))
    args = p.parse_args()
    try:
        run_audit(args)
    except (ValueError,OSError,RuntimeError,KeyError,TypeError) as exc:
        raise SystemExit('V4J_AUDIT_BLOCKED: '+str(exc)) from exc

if __name__=='__main__':
    cli()
