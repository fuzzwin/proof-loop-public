#!/usr/bin/env python3
"""Read-only consistency checks for attributed, case-scoped skill reviews."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def read_json(data):
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def shape(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError('expected exactly fields: ' + ', '.join(keys))


def text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('expected nonempty text')
    return value


def sha(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('expected lowercase SHA-256')
    return value


def regular(path):
    # Check the original path components before resolving; resolution hides symlinks.
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('symlink path: ' + str(path))
    if not path.is_file():
        raise ValueError('missing regular file: ' + str(path))
    with path.open('rb') as stream:
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError('file exceeds 1 MiB: ' + str(path))
    return data


def evaluate(root, contract_path, observations_path, expected_contract_sha256, approval=None):
    result = {'schema_version': 1, 'state': 'needs_evidence',
              'portable_acceptance': 'not_accepted', 'custody': 'unverified',
              'classification': 'attributed_case_review_not_authenticated_behavior_proof',
              'errors': [], 'packet_sha256': None, 'reviewer': None}
    seen = {}
    try:
        root = Path(root).absolute()
        if root.is_symlink() or any(p.is_symlink() for p in root.parents):
            raise ValueError('root must not contain symlinks')
        root = root.resolve(strict=True)

        def read(relative):
            text(relative)
            raw = Path(relative)
            if raw.is_absolute() or '..' in raw.parts or not raw.parts:
                raise ValueError('unsafe project path: ' + relative)
            path = root / raw
            data = regular(path)
            if path in seen and seen[path] != data:
                raise ValueError('file changed during inspection: ' + relative)
            seen[path] = data
            return data

        def bound_file(value):
            shape(value, ['path', 'sha256'])
            data = read(value['path'])
            if digest(data) != sha(value['sha256']):
                raise ValueError('hash mismatch: ' + value['path'])
            return data

        raw_contract = read(contract_path)
        if digest(raw_contract) != sha(expected_contract_sha256):
            raise ValueError('frozen contract hash mismatch')
        contract = read_json(raw_contract)
        shape(contract, ['schema_version', 'skill', 'requirements', 'cases'])
        if type(contract['schema_version']) is not int or contract['schema_version'] != 1:
            raise ValueError('unsupported contract version')
        bound_file(contract['skill'])
        if not isinstance(contract['requirements'], list) or not contract['requirements']:
            raise ValueError('requirements must be a nonempty list')
        for requirement in contract['requirements']:
            bound_file(requirement)
        if not isinstance(contract['cases'], list) or not 1 <= len(contract['cases']) <= 32:
            raise ValueError('cases must contain 1 through 32 cases')
        ids = []
        for case in contract['cases']:
            shape(case, ['id', 'prompt', 'criteria'])
            ids.append(text(case['id']))
            text(case['prompt'])
            if not isinstance(case['criteria'], list) or not case['criteria']:
                raise ValueError('case criteria must be a nonempty list')
            for criterion in case['criteria']:
                text(criterion)
        if len(set(ids)) != len(ids):
            raise ValueError('duplicate case IDs')
        observations = read_json(read(observations_path))
        shape(observations, ['schema_version', 'contract_sha256', 'candidate_sha256', 'cases'])
        if type(observations['schema_version']) is not int or observations['schema_version'] != 1:
            raise ValueError('unsupported observation version')
        if sha(observations['contract_sha256']) != expected_contract_sha256:
            raise ValueError('observations refer to a different contract')
        if sha(observations['candidate_sha256']) != contract['skill']['sha256']:
            raise ValueError('observations refer to a different candidate')
        if not isinstance(observations['cases'], list):
            raise ValueError('observed cases must be a list')
        observed_ids = []
        for case in observations['cases']:
            shape(case, ['id', 'outcome', 'artifacts', 'notes'])
            observed_ids.append(text(case['id']))
            text(case['notes'])
            if case['outcome'] != 'pass':
                raise ValueError('case did not pass: ' + case['id'])
            if not isinstance(case['artifacts'], list) or not case['artifacts']:
                raise ValueError('case has no observation artifacts: ' + case['id'])
            for artifact in case['artifacts']:
                bound_file(artifact)
        if sorted(observed_ids) != sorted(ids):
            raise ValueError('observations must cover each frozen case exactly once')
        # Include raw bytes of all inspected project files via their hashes. Any change
        # to a contract, observation, candidate, requirement or artifact invalidates approval.
        manifest = {str(p.relative_to(root)): digest(data) for p, data in seen.items()}
        packet = digest(canonical({'schema_version': 1, 'files': manifest}))
        result.update(state='ready_for_review', packet_sha256=packet)
        if approval is not None:
            approval_path = Path(approval).absolute()
            raw_approval = regular(approval_path)
            if approval_path.resolve().is_relative_to(root):
                raise ValueError('reviewer approval must be outside the project')
            approved = read_json(raw_approval)
            shape(approved, ['schema_version', 'packet_sha256', 'reviewer', 'decision', 'scope'])
            if type(approved['schema_version']) is not int or approved['schema_version'] != 1:
                raise ValueError('unsupported approval version')
            if sha(approved['packet_sha256']) != packet:
                raise ValueError('reviewer approval refers to a different packet')
            text(approved['reviewer'])
            if approved['decision'] != 'approve' or approved['scope'] != 'declared_cases':
                raise ValueError('reviewer has not approved the declared cases')
            if regular(approval_path) != raw_approval:
                raise ValueError('approval changed during inspection')
            result.update(state='reviewer_approved_for_cases', reviewer=approved['reviewer'])
        for path, original in seen.items():
            if regular(path) != original:
                raise ValueError('file changed during inspection: ' + str(path))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result.update(state='needs_evidence', errors=[str(exc)], reviewer=None)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--contract', required=True)
    parser.add_argument('--observations', required=True)
    parser.add_argument('--contract-sha256', required=True)
    parser.add_argument('--reviewer-approval')
    args = parser.parse_args()
    result = evaluate(args.root, args.contract, args.observations,
                      args.contract_sha256, args.reviewer_approval)
    print(json.dumps(result, sort_keys=True))
    return 2 if result['state'] == 'needs_evidence' else 0


if __name__ == '__main__':
    raise SystemExit(main())
