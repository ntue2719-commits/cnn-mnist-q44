#!/usr/bin/env python3
"""Read-only second-pass V4I source-byte replay and exact inventory audit.

Independently hashes files on a new pass, while reusing the candidate's
published validation rules; this is NOT independent G0/G6 release signoff.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import production_source_snapshot_v4i as inv

AUDIT_SCHEMA = 'CNN_PEP_V4I_SOURCE_INVENTORY_READONLY_REPLAY_AUDIT_V1'


def check_receipt(manifest: dict[str, Any], receipt: dict[str, Any],
                  inventory_sha256: str) -> None:
    inv.check(set(receipt) == {
        'schema','created_utc','source_inventory_sha256','source_identity_sha256',
        'source_preflight_receipt_sha256','adapter_independent_audit_sha256',
        'source_snapshot_materialized','build_once_executed','new_xsim_runs',
        'full400_authorized','release_gate'}, 'Unexpected V4I receipt fields')
    inv.check(receipt['schema'] == inv.RECEIPT_SCHEMA and
              type(receipt['created_utc']) is str and receipt['created_utc'].endswith('+00:00') and
              receipt['source_inventory_sha256'] == inventory_sha256 and
              receipt['source_identity_sha256'] == manifest['source_identity_sha256'] and
              receipt['source_preflight_receipt_sha256'] == manifest['source_preflight_receipt_sha256'] and
              receipt['adapter_independent_audit_sha256'] == manifest['adapter_independent_audit_sha256'] and
              receipt['source_snapshot_materialized'] is False and
              receipt['build_once_executed'] is False and
              receipt['new_xsim_runs'] == 0 and
              receipt['full400_authorized'] is False and
              receipt['release_gate'] == inv.STOP,
              'V4I receipt hash/authorization drift')


def audit_model(manifest: dict[str, Any], receipt: dict[str, Any],
                original_sha256: str, expected: dict[str, Any]) -> None:
    inv.check(manifest == expected, 'Inventory source, hash or gate differs from live authoritative bytes')
    inv.check(set(manifest) == set(expected) and
              manifest['schema'] == inv.SCHEMA and
              manifest['source_snapshot_materialized'] is False and
              manifest['compiled_build_snapshot_exists'] is False and
              manifest['build_once_executed'] is False and
              manifest['windows_job_object_exercised'] is False and
              manifest['fi_results_committed'] == 0 and
              manifest['xsim_runs'] == 0 and
              manifest['G6_production_qualified'] is False and
              manifest['G7_scientific_approved'] is False and
              manifest['full400_authorized'] is False and
              manifest['full400_executed'] is False and
              manifest['release_gate'] == inv.STOP,
              'Unsafe V4I scope/approval flag')
    check_receipt(manifest, receipt, original_sha256)


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    inv.check(args.mode == 'audit' and not args.execute and not args.approve,
              'V4I AUDIT DENIED BEFORE IO: no build/execute/approval')
    inv.no_link_chain(args.repo.absolute())
    repo = args.repo.resolve()
    evidence = {k: inv.dir_arg(repo, getattr(args, k + '_dir'), prefix, existing=True)
                for k, prefix in (('source','v4f4f_'),('review','v4f4f_'),
                                  ('history','v4f4f_'),('rehearsal','v4f4f_'),
                                  ('plan','v4f4f_'),('adapter','v4h_exec_'),
                                  ('adapter_audit','v4h_exec_'))}
    source_dir = inv.dir_arg(repo, args.inventory_dir, 'v4i_', existing=True)
    out = inv.dir_arg(repo, args.out_dir, 'v4i_', existing=False)
    inv.check({p.name for p in source_dir.iterdir()} ==
              {'v4i_source_inventory.json','v4i_source_inventory_receipt.json'} and
              all(p.is_file() and not p.is_symlink() for p in source_dir.iterdir()),
              'Unexpected/orphan V4I inventory files')
    file = source_dir / 'v4i_source_inventory.json'
    rec_file = source_dir / 'v4i_source_inventory_receipt.json'
    manifest = inv.read_object(file)
    receipt = inv.read_object(rec_file)
    inv.check(file.read_bytes() == inv.canonical(manifest) and
              rec_file.read_bytes() == inv.canonical(receipt),
              'V4I result must be byte-exact canonical UTF-8/LF')
    raw_project = args.project if args.project.is_absolute() else repo / args.project
    raw_vivado = args.vivado if args.vivado.is_absolute() else repo / args.vivado
    raw_prj = args.prj if args.prj.is_absolute() else repo / args.prj
    for raw in (raw_project, raw_vivado, raw_prj):
        inv.no_link_chain(raw.absolute())
    project, vivado, prj = raw_project.resolve(), raw_vivado.resolve(), raw_prj.resolve()
    expected = inv.collect(repo, project, vivado, prj, evidence)
    audit_model(manifest, receipt, inv.sha_file(file), expected)
    result = {
        'schema': AUDIT_SCHEMA,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'source_inventory_sha256': inv.sha_file(file),
        'source_inventory_receipt_sha256': inv.sha_file(rec_file),
        'source_identity_sha256': manifest['source_identity_sha256'],
        'compiled_sources_rehashed': manifest['source_count'],
        'v4f4f_asset_and_source_pins_rehashed': manifest['v4f4f_input_pin_count'],
        'frozen_rehashed': 4,
        'toolchain_files_rehashed': len(manifest['toolchain_sha256']),
        'read_only_rehash_replay': True,
        'independently_qualified_g6': False,
        'build_once_executed': False,
        'new_xsim_runs': 0,
        'full400_authorized': False,
        'release_gate': inv.STOP,
    }
    out.mkdir(parents=False, exist_ok=False)
    inv.write_new(out / 'v4i_source_inventory_audit.json', inv.canonical(result))
    print(f'V4I_SOURCE_INVENTORY_AUDIT_PASS: {result["compiled_sources_rehashed"]} compiled sources; '
          f'{result["v4f4f_asset_and_source_pins_rehashed"]} V4f4F pins; '
          '0 build; 0 XSim; G6 NOT QUALIFIED; FULL400 BLOCKED', flush=True)
    print('V4I_AUDIT_OUTPUT: ' + str(out), flush=True)
    return result


def cli() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('audit','build','execute','full400'), default='audit')
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
    p.add_argument('--inventory-dir', type=Path, default=Path('fi/results/v4i_source_snapshot_01'))
    p.add_argument('--out-dir', type=Path, default=Path('fi/results/v4i_source_snapshot_audit_01'))
    p.add_argument('--execute', action='store_true')
    p.add_argument('--approve', action='store_true')
    args = p.parse_args()
    try:
        run_audit(args)
    except (ValueError, OSError, RuntimeError, TypeError, KeyError) as exc:
        raise SystemExit('V4I_SOURCE_INVENTORY_AUDIT_BLOCKED: ' + str(exc)) from exc

if __name__ == '__main__':
    cli()
