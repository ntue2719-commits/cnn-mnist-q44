#!/usr/bin/env python3
"""CNN_PEP V4f Phase 4A: scientific-protocol review and manual seal gate.

PREPARE pins the previously audited V4f3 science contract, frozen Pilot400
inputs, and explicit tri-state observability semantics. It does not freeze G7.
SEAL requires separate explicit operator and independent-reviewer attestations
of the exact same SHA256. VERIFY rechecks all hashes and provenance.

No mode invokes Vivado, injects a fault, qualifies the Full400 runner, or
issues Full400 release authorization. Attestations are manual assertions, not
cryptographic identity/signature verification.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
for directory in (HERE, HERE.parents[1] / 'scripts', HERE.parents[1] / 'v3b/scripts'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import review_release_v4f3 as v4f3
import qualify_full400_v4f as v4f
from pilot400_common import sha256

SCHEMA_CORE = 'CNN_PEP_V4F4_SCIENTIFIC_PROTOCOL_CORE_V1'
SCHEMA_PREPARE = 'CNN_PEP_V4F4_G7_PREPARE_V1'
SCHEMA_SEAL = 'CNN_PEP_V4F4_G7_MANUAL_SEAL_V1'
SCHEMA_ATTESTATION = 'CNN_PEP_V4F4_MANUAL_PROTOCOL_ATTESTATION_V1'
CANDIDATE_SHA256 = '1336932af759a8a368c20738142421a96a3841cf0df6025d8c7356202823c9bf'
PREPARED_STATUS = 'PREPARED_AWAITING_INDEPENDENT_APPROVAL_FULL400_BLOCKED'
SEALED_STATUS = 'SCIENCE_PROTOCOL_SEALED_MANUAL_ATTESTATIONS_FULL400_BLOCKED'
APPROVE_DECISION = 'APPROVE_SCIENTIFIC_PROTOCOL_ONLY'
ATTEST_TEXT = ('I reviewed this exact SHA256-pinned scientific protocol and its '
               'directed-sampling limitations. This is not Full400 execution approval.')
CHECKS = (
    'research_questions', 'outcome_priority_and_harmful_definition',
    'l0_l1_l2_observability', 'missingness_and_censoring',
    'directed_nonrandom_population', 'frozen_source_and_tuples',
    'scientific_claim_limits', 'no_full400_execution_authorization',
)
OUTCOMES = (
    'NONCOMPLETION', 'PROTOCOL_VIOLATION', 'WRONG_LABEL',
    'WRONG_LOGITS_SAME_LABEL', 'FINAL_OUTPUT_MATCH',
)
STAGE_VALUES = ('MATCH', 'MISMATCH', 'NOT_OBSERVED', 'NOT_APPLICABLE', 'INCONCLUSIVE')
UNKNOWN = 'INCONCLUSIVE'

# These conditions are declarative and reviewed as part of the protocol core.
SEMANTICS = {
    'analysis_unit': 'one unique frozen scenario_id, not each replay/run',
    'stage_checkpoint_scope': ['POOL1', 'POOL2'],
    'stage_categories': list(STAGE_VALUES),
    'truth_domain': ['TRUE', 'FALSE', 'INCONCLUSIVE'],
    'L0': 'TRUE for NONCOMPLETION, PROTOCOL_VIOLATION, WRONG_LABEL; else FALSE',
    'L1': 'TRUE for L0 or WRONG_LOGITS_SAME_LABEL; FALSE for FINAL_OUTPUT_MATCH',
    'L2': ('TRUE if L1 TRUE or any predeclared checkpoint MISMATCH; '
           'FALSE only if L1 FALSE and all predeclared checkpoints measured MATCH; '
           'otherwise INCONCLUSIVE'),
    'invalid_infrastructure_result': 'NOT_CLASSIFIABLE; never map to NONCOMPLETION',
    'masked_vs_unexposed': 'FINAL_OUTPUT_MATCH does not establish masking or nonexposure',
    'population_claim': 'directed selected RTL tuples, never random physical SEU incidence',
    'full400_authorized': False,
}


def insist(test: bool, message: str) -> None:
    if not test:
        raise ValueError(message)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False,
                       allow_nan=False) + '\n').encode('utf-8')


def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for k, v in pairs:
        insist(k not in output, 'Duplicate JSON key: ' + k)
        output[k] = v
    return output


def strict_json(path: Path) -> dict[str, Any]:
    insist(path.is_file() and not path.is_symlink(), 'Missing or symlinked JSON: ' + str(path))
    obj = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=no_duplicate_keys)
    insist(isinstance(obj, dict), 'Expected JSON object: ' + str(path))
    return obj


def write_new(path: Path, payload: bytes) -> None:
    insist(not path.exists() and not path.is_symlink(), 'Refusing overwrite: ' + str(path))
    with path.open('xb') as handle:
        handle.write(payload)
        handle.flush()


def load_candidate(repo: Path) -> tuple[dict[str, Any], str]:
    candidate, digest = v4f3.load_protocol(repo)
    insist(digest == CANDIDATE_SHA256, 'Unreviewed V4f3 science candidate SHA256')
    insist(candidate['outcome_priority'] == list(OUTCOMES), 'Outcome priority differs')
    insist(candidate['design']['planned_scenarios'] == 400, '400 plan altered')
    insist(candidate['design']['dat0010_pinned_pair'] == ['bit0@1340', 'bit1@1340'],
           'DAT_0010 tuple altered')
    insist(candidate['missing_value_policy']['not_observed_is_not_zero'] is True,
           'Missingness must not become zero')
    insist(candidate['scientific_limits']['physical_seu_population_claim'] is False,
           'Physical SEU inference forbidden')
    return candidate, digest


def validate_v4f3(repo: Path, review_dir: Path) -> tuple[dict[str, Any], str]:
    candidate, digest = load_candidate(repo)
    path = review_dir / 'v4f3_release_review.json'
    review = strict_json(path)
    insist(review.get('schema') == v4f3.SCHEMA and
           review.get('status') == v4f3.STATUS and
           review.get('scientific_protocol_candidate_sha256') == digest and
           review.get('scientific_protocol_status') == candidate['status'] and
           review.get('source_authority') == 'vivado' and
           review.get('new_xsim_runs_during_review') == 0 and
           review.get('scientific_protocol_frozen') is False and
           review.get('full400_runner_qualified') is False and
           review.get('full400_authorized') is False and
           review.get('full400_executed') is False and
           review.get('original_frozen_files_modified') is False and
           review.get('target_status_modified') is False and
           review.get('gates') == v4f3.GATE_STATUSES and
           review.get('scope', {}).get('frozen_plan') == 400,
           'V4f3 review does not support G7 preparation')
    mirror = strict_json(review_dir / 'scientific_protocol_candidate.json')
    insist(mirror == candidate, 'V4f3 saved science candidate differs from source')
    table = (review_dir / 'g0_g7_review.csv').read_text(encoding='utf-8').splitlines()
    import csv
    with (review_dir / 'g0_g7_review.csv').open(newline='', encoding='utf-8') as handle:
        rows = list(csv.DictReader(handle))
    insist(len(rows) == 8 and
           {r.get('gate'): r.get('status') for r in rows} == v4f3.GATE_STATUSES and
           all(set(r) == {'gate', 'status'} for r in rows) and
           len(table) >= 9,
           'V4f3 G0-G7 review matrix changed')
    for rel, pinned in v4f.FROZEN_HASHES.items():
        path = repo / rel
        insist(path.is_file() and sha256(path) == pinned,
               'Frozen source mismatch: ' + rel)
    return review, sha256(path=review_dir / 'v4f3_release_review.json')


def protocol_core(repo: Path) -> dict[str, Any]:
    candidate, digest = load_candidate(repo)
    science = deepcopy(candidate)
    del science['status']
    del science['approval']
    return {
        'schema': SCHEMA_CORE,
        'v4f3_candidate_source_sha256': digest,
        'scientific_content': science,
        'formal_observation_semantics': deepcopy(SEMANTICS),
        'frozen_pilot400_hashes': dict(v4f.FROZEN_HASHES),
        'explicit_release_authority': False,
    }


def validate_output(repo: Path, out: Path) -> None:
    parent = repo / 'fi/results'
    insist(parent.is_dir() and not parent.is_symlink() and
           out.parent == parent and out.name.startswith('v4f4_') and
           not out.exists() and not out.is_symlink(),
           'Output must be a new fi/results/v4f4_* sibling (no overwrite)')


def prepare(repo: Path, review_dir: Path, out: Path) -> dict[str, Any]:
    validate_output(repo, out)
    review, review_sha = validate_v4f3(repo, review_dir)
    core = protocol_core(repo)
    core_blob = json_bytes(core)
    digest = sha_bytes(core_blob)
    script_sha = sha256(Path(__file__).resolve())
    templates: dict[str, dict[str, Any]] = {}
    for role in ('operator', 'independent_reviewer'):
        templates[role] = {
            'schema': SCHEMA_ATTESTATION,
            'role': role,
            'reviewer_id': '',
            'decision': 'PENDING',
            'protocol_sha256': digest,
            'v4f3_review_sha256': review_sha,
            'reviewed_utc': '',
            'attestation': ATTEST_TEXT,
            'checklist': {key: False for key in CHECKS},
        }
    record = {
        'schema': SCHEMA_PREPARE,
        'status': PREPARED_STATUS,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'candidate_sha256': CANDIDATE_SHA256,
        'v4f3_review_sha256': review_sha,
        'v4f3_status': review['status'],
        'core_sha256': digest,
        'code_sha256': script_sha,
        'frozen_hashes': dict(v4f.FROZEN_HASHES),
        'template_sha256': {role: sha_bytes(json_bytes(val))
                            for role, val in templates.items()},
        'G7': 'OPEN_AWAITING_TWO_SEPARATE_MANUAL_ATTESTATIONS',
        'full400_runner_qualified': False,
        'full400_authorized': False,
        'full400_fi_executed': False,
        'new_xsim_runs': 0,
    }
    out.mkdir(parents=False, exist_ok=False)
    write_new(out / 'protocol_core.json', core_blob)
    write_new(out / 'v4f4_prepare.json', json_bytes(record))
    for role, template in templates.items():
        write_new(out / ('TEMPLATE_' + role + '.json'), json_bytes(template))
    write_new(out / 'PROTOCOL_CORE_SHA256.txt', (digest + '  protocol_core.json\n').encode('ascii'))
    print('V4F4_G7_PREPARE_PASS: protocol core SHA256 pinned; two human attestations PENDING; 0 XSim; FULL400 BLOCKED', flush=True)
    print('V4F4_OUTPUT: ' + str(out), flush=True)
    return record


def check_prepared(repo: Path, review_dir: Path, prepared: Path) -> tuple[dict, dict]:
    insist(prepared.is_dir() and not prepared.is_symlink() and
           prepared.parent == repo / 'fi/results' and prepared.name.startswith('v4f4_'),
           'Preflight dir outside fi/results/v4f4_*')
    review, review_sha = validate_v4f3(repo, review_dir)
    obj = strict_json(prepared / 'v4f4_prepare.json')
    insist(obj.get('schema') == SCHEMA_PREPARE and obj.get('status') == PREPARED_STATUS and
           obj.get('candidate_sha256') == CANDIDATE_SHA256 and
           obj.get('v4f3_review_sha256') == review_sha and
           obj.get('v4f3_status') == review['status'] and
           obj.get('code_sha256') == sha256(Path(__file__).resolve()) and
           obj.get('frozen_hashes') == v4f.FROZEN_HASHES and
           obj.get('G7') == 'OPEN_AWAITING_TWO_SEPARATE_MANUAL_ATTESTATIONS' and
           obj.get('full400_authorized') is False and
           obj.get('full400_runner_qualified') is False and
           obj.get('full400_fi_executed') is False and
           obj.get('new_xsim_runs') == 0,
           'V4f4 preparation metadata inconsistent with source')
    core = protocol_core(repo)
    core_blob = json_bytes(core)
    insist((prepared / 'protocol_core.json').read_bytes() == core_blob and
           obj.get('core_sha256') == sha_bytes(core_blob) and
           (prepared / 'PROTOCOL_CORE_SHA256.txt').read_text(encoding='ascii') ==
                obj['core_sha256'] + '  protocol_core.json\n',
           'V4f4 core payload or digest changed')
    for role in ('operator', 'independent_reviewer'):
        template = strict_json(prepared / ('TEMPLATE_' + role + '.json'))
        insist(template == {
            'schema': SCHEMA_ATTESTATION, 'role': role,
            'reviewer_id': '', 'decision': 'PENDING',
            'protocol_sha256': obj['core_sha256'],
            'v4f3_review_sha256': review_sha,
            'reviewed_utc': '', 'attestation': ATTEST_TEXT,
            'checklist': {key: False for key in CHECKS},
        } and obj.get('template_sha256', {}).get(role) ==
           sha_bytes(json_bytes(template)) and
           (prepared / ('TEMPLATE_' + role + '.json')).read_bytes() == json_bytes(template),
           'Preparation attestation template changed: ' + role)
    return obj, core


def assess_attestation(path: Path, expected_role: str, core_hash: str,
                       review_hash: str) -> dict[str, Any]:
    receipt = strict_json(path)
    insist(set(receipt) == {'schema', 'role', 'reviewer_id', 'decision',
                            'protocol_sha256', 'v4f3_review_sha256',
                            'reviewed_utc', 'attestation', 'checklist'},
           'Manual attestation fields changed')
    insist(receipt['schema'] == SCHEMA_ATTESTATION and
           receipt['role'] == expected_role and
           receipt['decision'] == APPROVE_DECISION and
           receipt['protocol_sha256'] == core_hash and
           receipt['v4f3_review_sha256'] == review_hash and
           receipt['attestation'] == ATTEST_TEXT and
           isinstance(receipt['reviewer_id'], str) and
           3 <= len(receipt['reviewer_id'].strip()) <= 128 and
           receipt['reviewer_id'] == receipt['reviewer_id'].strip() and
           re.fullmatch(r'[\w .@-]+', receipt['reviewer_id']) is not None and
           type(receipt['checklist']) is dict and
           set(receipt['checklist']) == set(CHECKS) and
           all(value is True for value in receipt['checklist'].values()),
           'Manual approval missing, unreviewed, or wrong hash/role')
    try:
        when = datetime.fromisoformat(str(receipt['reviewed_utc']).replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Malformed attestation UTC timestamp') from exc
    insist(when.tzinfo is not None and when.utcoffset().total_seconds() == 0 and
           when.year >= 2026,
           'Attestation timestamp must be real UTC with time zone')
    return receipt


def seal(repo: Path, review_dir: Path, prepared: Path, out: Path,
         operator_file: Path, independent_file: Path) -> dict[str, Any]:
    validate_output(repo, out)
    meta, core = check_prepared(repo, review_dir, prepared)
    insist(operator_file.resolve() != independent_file.resolve() and
           prepared not in operator_file.resolve().parents and
           prepared not in independent_file.resolve().parents,
           'Manual attestations must be separate files outside the immutable prepared evidence')
    operator = assess_attestation(operator_file, 'operator', meta['core_sha256'],
                                  meta['v4f3_review_sha256'])
    reviewer = assess_attestation(independent_file, 'independent_reviewer',
                                  meta['core_sha256'], meta['v4f3_review_sha256'])
    insist(operator['reviewer_id'].casefold() != reviewer['reviewer_id'].casefold(),
           'Operator and independent reviewer must be different individuals')
    record = {
        'schema': SCHEMA_SEAL, 'status': SEALED_STATUS,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'prepared_sha256': sha256(prepared / 'v4f4_prepare.json'),
        'v4f3_review_sha256': meta['v4f3_review_sha256'],
        'core_sha256': meta['core_sha256'],
        'operator_receipt_sha256': sha_bytes(json_bytes(operator)),
        'independent_receipt_sha256': sha_bytes(json_bytes(reviewer)),
        'approval_kind': 'EXPLICIT_MANUAL_ATTESTATIONS_NOT_CRYPTOGRAPHIC_SIGNATURES',
        'identity_authentication': 'NOT_PERFORMED_BY_THIS_SCRIPT',
        'G7_protocol_content': 'SEALED_WITH_TWO_DISTINCT_MANUAL_ATTESTATIONS',
        'G0': 'NOT_APPROVED_BY_THIS_STAGE',
        'G5': 'NOT_APPROVED_BY_THIS_STAGE',
        'G6': 'NOT_APPROVED_BY_THIS_STAGE',
        'G0_G7_final_release': 'NOT_GRANTED',
        'new_xsim_runs': 0,
        'full400_runner_qualified': False,
        'full400_authorized': False,
        'full400_fi_executed': False,
    }
    out.mkdir(parents=False, exist_ok=False)
    write_new(out / 'protocol_core.json', json_bytes(core))
    write_new(out / 'operator_attestation.json', json_bytes(operator))
    write_new(out / 'independent_attestation.json', json_bytes(reviewer))
    write_new(out / 'v4f4_seal.json', json_bytes(record))
    print('V4F4_PROTOCOL_MANUAL_SEAL_PASS: two distinct attestation receipts bound to exact SHA256; identity not authenticated; 0 XSim; FULL400 BLOCKED', flush=True)
    print('V4F4_OUTPUT: ' + str(out), flush=True)
    return record


def verify_seal(repo: Path, review_dir: Path, prepared: Path,
                sealed: Path) -> dict[str, Any]:
    meta, core = check_prepared(repo, review_dir, prepared)
    insist(sealed.is_dir() and sealed.parent == repo / 'fi/results' and
           sealed.name.startswith('v4f4_') and not sealed.is_symlink(),
           'Seal directory must belong to fi/results/v4f4_*')
    obj = strict_json(sealed / 'v4f4_seal.json')
    operator_path = sealed / 'operator_attestation.json'
    reviewer_path = sealed / 'independent_attestation.json'
    operator = assess_attestation(operator_path, 'operator', meta['core_sha256'],
                                  meta['v4f3_review_sha256'])
    reviewer = assess_attestation(reviewer_path, 'independent_reviewer',
                                  meta['core_sha256'], meta['v4f3_review_sha256'])
    insist(operator['reviewer_id'].casefold() != reviewer['reviewer_id'].casefold(),
           'Seal has self-review')
    insist(operator_path.read_bytes() == json_bytes(operator) and
           reviewer_path.read_bytes() == json_bytes(reviewer) and
           (sealed / 'v4f4_seal.json').read_bytes() == json_bytes(obj),
           'Sealed record/receipt bytes changed')
    insist(obj.get('schema') == SCHEMA_SEAL and obj.get('status') == SEALED_STATUS and
           obj.get('prepared_sha256') == sha256(prepared / 'v4f4_prepare.json') and
           obj.get('v4f3_review_sha256') == meta['v4f3_review_sha256'] and
           obj.get('core_sha256') == meta['core_sha256'] and
           obj.get('operator_receipt_sha256') == sha_bytes(json_bytes(operator)) and
           obj.get('independent_receipt_sha256') == sha_bytes(json_bytes(reviewer)) and
           obj.get('approval_kind') ==
              'EXPLICIT_MANUAL_ATTESTATIONS_NOT_CRYPTOGRAPHIC_SIGNATURES' and
           obj.get('identity_authentication') == 'NOT_PERFORMED_BY_THIS_SCRIPT' and
           obj.get('G0_G7_final_release') == 'NOT_GRANTED' and
           obj.get('full400_authorized') is False and
           obj.get('full400_runner_qualified') is False and
           obj.get('full400_fi_executed') is False and
           obj.get('new_xsim_runs') == 0 and
           (sealed / 'protocol_core.json').read_bytes() == json_bytes(core),
           'V4f4 sealed protocol/release evidence altered')
    print('V4F4_PROTOCOL_SEAL_VERIFY_PASS: content and manual receipts consistent; 0 XSim; FULL400 BLOCKED', flush=True)
    return obj


def level_eval(outcome: str, checkpoints: dict[str, str]) -> dict[str, str]:
    """Pure 3-valued model for a selected scenario, not a hardware detector.

    Checkpoints must name both predeclared stages. Missing is not FALSE.
    WRONG_LOGITS_SAME_LABEL is numerical corruption, not harmful wrong label.
    """
    insist(outcome in OUTCOMES, 'Unknown/invalid design outcome: ' + str(outcome))
    insist(isinstance(checkpoints, dict) and set(checkpoints) == {'POOL1', 'POOL2'} and
           all(v in STAGE_VALUES for v in checkpoints.values()),
           'Checkpoint values must explicitly distinguish MATCH/MISMATCH/missing')
    l0 = outcome in OUTCOMES[:3]
    l1 = outcome != 'FINAL_OUTPUT_MATCH'
    if l1 or any(v == 'MISMATCH' for v in checkpoints.values()):
        l2 = 'TRUE'
    elif all(v == 'MATCH' for v in checkpoints.values()):
        l2 = 'FALSE'
    else:
        l2 = UNKNOWN
    return {
        'L0': 'TRUE' if l0 else 'FALSE',
        'L1': 'TRUE' if l1 else 'FALSE',
        'L2': l2,
        'L1_over_L0': 'TRUE' if l1 and not l0 else 'FALSE',
        'L2_over_L1': 'TRUE' if l2 == 'TRUE' and not l1 else
                     ('FALSE' if l1 or l2 == 'FALSE' else UNKNOWN),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--mode', required=True, choices=['prepare', 'seal', 'verify'])
    parser.add_argument('--v4f3-dir', type=Path,
                        default=Path('fi/results/v4f3_release_review_01'))
    parser.add_argument('--prepared-dir', type=Path)
    parser.add_argument('--sealed-dir', type=Path)
    parser.add_argument('--operator-file', type=Path)
    parser.add_argument('--independent-file', type=Path)
    parser.add_argument('--out-dir', type=Path)
    parser.add_argument('--execute', action='store_true',
                        help='Forbidden: V4f4 never invokes Vivado')
    parser.add_argument('--authorize-full400', action='store_true',
                        help='Forbidden: V4f4 never authorizes Full400')
    args = parser.parse_args()
    try:
        insist(not args.execute and not args.authorize_full400,
               'V4f4 never executes/authorizes Full400')
        repo = args.repo.resolve()
        loc = lambda p: p.resolve() if p.is_absolute() else (repo / p).resolve()
        review = loc(args.v4f3_dir)
        if args.mode == 'prepare':
            insist(args.out_dir is not None, '--out-dir required for prepare')
            prepare(repo, review, loc(args.out_dir))
        elif args.mode == 'seal':
            insist(all(x is not None for x in
                       (args.prepared_dir, args.out_dir, args.operator_file,
                        args.independent_file)),
                   'Seal requires prepared dir, out dir and two manual attestation files')
            seal(repo, review, loc(args.prepared_dir), loc(args.out_dir),
                 loc(args.operator_file), loc(args.independent_file))
        else:
            insist(args.prepared_dir is not None and args.sealed_dir is not None,
                   'Verify requires --prepared-dir and --sealed-dir')
            verify_seal(repo, review, loc(args.prepared_dir), loc(args.sealed_dir))
    except (ValueError, RuntimeError, OSError, TypeError, KeyError) as exc:
        raise SystemExit('V4F4_FAIL: ' + str(exc)) from exc


if __name__ == '__main__':
    main()
