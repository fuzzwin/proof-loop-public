#!/usr/bin/env python3
"""Proof Loop portable project workflow CLI (standard library only)."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import platform
import tempfile
import time
from datetime import datetime, timezone, timedelta

VERSION = "0.2.2"
PROOF_DIR = ".proof"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def json_equal(left, right) -> bool:
    """Compare exact JSON projections without Python's bool/number coercion.

    Object key order and whitespace do not matter; scalar representations do.
    In particular, true is not 1, even inside a nested ID value.
    """
    return canonical_json(left) == canonical_json(right)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def project_root(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {root}")
    return root


def proof_root(root: Path) -> Path:
    return root / PROOF_DIR


def load_json(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def atomic_write(path: Path, data: bytes, *, expected_sha256: str | None = None, exclusive: bool = False, mode: int = 0o600) -> None:
    """Atomic same-directory write with optional compare-and-swap precondition."""
    if path.is_symlink():
        raise ValueError(f"refusing to write through symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and path.exists():
        raise ValueError(f"refusing to overwrite existing file: {path}")
    if expected_sha256 is not None and (not path.is_file() or sha256(path) != expected_sha256):
        raise ValueError(f"stale write rejected; file changed since it was read: {path}")
    fd, temp_name = tempfile.mkstemp(prefix=".proofloop-", dir=path.parent)
    temp = Path(temp_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            try:
                os.link(temp, path)
            except FileExistsError as exc:
                raise ValueError(f"refusing to overwrite existing file: {path}") from exc
            temp.unlink()
        else:
            if expected_sha256 is not None and (not path.is_file() or sha256(path) != expected_sha256):
                raise ValueError(f"stale write rejected; file changed before commit: {path}")
            os.replace(temp, path)
        try:
            dfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    finally:
        if temp.exists():
            temp.unlink()


def write_json(path: Path, value, *, expected_sha256: str | None = None, exclusive: bool = False, mode: int = 0o600) -> None:
    atomic_write(path, canonical_json(value).encode("utf-8"), expected_sha256=expected_sha256, exclusive=exclusive, mode=mode)


@contextmanager
def project_lock(root: Path):
    """Cross-process exclusive lock. A leftover lock blocks writes for manual recovery."""
    if proof_root(root).is_symlink():
        raise ValueError(".proof must not be a symlink")
    lock_path = root / ".proof.write.lock"
    if lock_path.is_symlink():
        raise ValueError(".proof.write.lock must not be a symlink")
    payload = canonical_json({"pid": os.getpid(), "created_at": now()}).encode()
    deadline = time.monotonic() + 5.0
    fd = None
    while fd is None:
        try:
            fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ValueError("project write lock is held; inspect and remove a stale .proof.write.lock only after confirming no writer is active")
            time.sleep(0.05)
    try:
        inode = os.fstat(fd).st_ino
        os.write(fd, payload)
        os.fsync(fd)
        yield
    finally:
        os.close(fd)
        try:
            if lock_path.stat().st_ino == inode:
                lock_path.unlink()
        except FileNotFoundError:
            pass


def append_event(root: Path, run_id: str, event: str, **details) -> None:
    path = proof_root(root) / "ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": now(), "run_id": run_id, "event": event, **details}
    if path.is_symlink():
        raise ValueError(".proof/ledger.jsonl must not be a symlink")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def events(root: Path, run_id: str | None = None):
    path = proof_root(root) / "ledger.jsonl"
    if path.is_symlink():
        raise ValueError(".proof/ledger.jsonl must not be a symlink")
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [row for row in rows if run_id is None or row.get("run_id") == run_id]


def run_dir(root: Path, run_id: str) -> Path:
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("run id must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}")
    base = proof_root(root)
    runs = base / "runs"
    if base.is_symlink() or runs.is_symlink():
        raise ValueError(".proof and .proof/runs must not be symlinks")
    path = runs / run_id
    if path.is_symlink():
        raise ValueError(f"run directory must not be a symlink: {run_id}")
    resolved = path.resolve()
    try:
        resolved.relative_to(runs.resolve())
    except ValueError as exc:
        raise ValueError(f"run path escapes .proof/runs: {run_id}") from exc
    if not resolved.is_dir():
        raise ValueError(f"unknown run: {run_id}")
    for name in ("run-card.json", "input-manifest.json", "receipt.json"):
        if (resolved / name).is_symlink():
            raise ValueError(f"run file must not be a symlink: {name}")
    return resolved


def safe_project_file(root: Path, relative: str) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or not relative or any(part == ".." for part in raw.parts):
        raise ValueError(f"input must be project-relative: {relative}")
    cursor = root
    for part in raw.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"input path contains a symlink: {relative}")
    resolved = (root / raw).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"input escapes project root: {relative}") from exc
    if not resolved.is_file():
        raise ValueError(f"input file does not exist: {relative}")
    return resolved


def snapshot(root: Path, files: list[str]):
    result = {}
    for relative in sorted(set(files)):
        path = safe_project_file(root, relative)
        result[relative] = {"sha256": sha256(path), "bytes": path.stat().st_size}
    return result


def _cmd_init_locked(args):
    root = project_root(args.root)
    base = proof_root(root)
    if base.exists() and not args.force:
        raise ValueError(f"{base} already exists; use --force only to fill missing starter files")
    (base / "playbooks").mkdir(parents=True, exist_ok=True)
    (base / "runs").mkdir(parents=True, exist_ok=True)
    starters = {
        "BRIEF.md": "# Project index\n\nThis file is an index and evidence pointer, not the source of truth for project data.\n\n## Owning source files\n- Add project-relative paths and roles here.\n\n## Acceptance criteria and tests\n- Point to versioned acceptance criteria and test files; preserve their hashes per run.\n\n## Unrecorded decisions\n- Record only decisions that are not captured in an owning source file.\n\n## Last run / receipt pointer\n- None\n",
        "capabilities.json": {"schema_version": 2, "selection_order": ["policy", "fit", "health", "ranking"], "capabilities": [{"id": "python-stdlib", "policy": "allow", "lifecycle": "tested", "trust": "earned-by-evidence", "evidence": "Python standard library; verify with python3 --version", "use_when": ["local deterministic checks"], "not_for": ["security isolation"], "license_status": "not_applicable", "telemetry_status": "not_applicable", "health_check": "python3 --version", "version_or_pin": platform.python_version(), "last_reviewed": now()}]},
        "playbooks/build.md": "# Build playbook\n\n1. Define scope and acceptance checks in the Run Card.\n2. Record declared input files and hashes.\n3. Make the smallest reversible change.\n4. Run the declared mechanical checks.\n5. Ask a fresh verifier to inspect requirements and primary evidence before the author summary.\n6. Complete a receipt; unresolved material claims remain Unknown or Blocked.\n",
    }
    for name, value in starters.items():
        dest = base / name
        if dest.exists():
            continue
        if isinstance(value, dict):
            write_json(dest, value, exclusive=True)
        else:
            atomic_write(dest, value.encode("utf-8"), exclusive=True)
    manifest = base / "manifest.json"
    if not manifest.exists():
        write_json(manifest, {"schema_version": 1, "roles": {}, "note": "Declare role-to-path ownership locally; run manifests record input hashes."}, exclusive=True)
    print(f"Initialized Proof Loop {VERSION} at {base}")
    return 0


def cmd_init(args):
    root = project_root(args.root)
    with project_lock(root):
        return _cmd_init_locked(args)


def cmd_validate_registry(args):
    root = project_root(args.root)
    if proof_root(root).is_symlink():
        raise ValueError(".proof must not be a symlink")
    path = safe_project_file(root, ".proof/capabilities.json")
    registry = load_json(path)
    errors = []
    if not isinstance(registry, dict):
        print(canonical_json({"valid": False, "status": "BLOCKED", "capability_count": 0, "errors": ["registry must be an object"]}), end="")
        return 2
    if registry.get("schema_version") != 2:
        errors.append("schema_version must be 2")
    order = registry.get("selection_order", [])
    if not isinstance(order, list) or not order or order[0] != "policy":
        errors.append("selection_order must apply policy before fit/ranking")
    if not isinstance(order, list) or any(not isinstance(x, str) for x in order) or (isinstance(order, list) and all(isinstance(x, str) for x in order) and len(order) != len(set(order))):
        errors.append("selection_order must be a unique ordered list")
    allowed = ["discovered", "reviewed", "installed", "authenticated", "tested", "enabled"]
    caps = registry.get("capabilities", [])
    if not isinstance(caps, list):
        errors.append("capabilities must be a list")
        caps = []
    ids = [c.get("id") for c in caps if isinstance(c, dict)]
    if any(not isinstance(item, str) for item in ids) or len(ids) != len(set(ids)):
        errors.append("capability IDs must be unique")
    for index, cap in enumerate(caps):
        if not isinstance(cap, dict):
            errors.append(f"capability {index} must be an object")
            continue
        if cap.get("lifecycle") not in allowed:
            errors.append(f"capability {index} lifecycle must be one of: {', '.join(allowed)}")
        for key in ("id", "evidence", "health_check", "license_status", "telemetry_status", "policy"):
            if not cap.get(key):
                errors.append(f"capability {index} is missing {key}")
        if cap.get("policy") not in {"allow", "deny", "review"}:
            errors.append(f"capability {index} policy must be allow, deny, or review")
        for key in ("use_when", "not_for"):
            if not isinstance(cap.get(key), list):
                errors.append(f"capability {index} {key} must be a list")
        if cap.get("lifecycle") in {"installed", "authenticated", "tested", "enabled"} and not cap.get("version_or_pin"):
            errors.append(f"capability {index} needs version_or_pin after installation")
        if cap.get("lifecycle") == "enabled" and not cap.get("rollback"):
            errors.append(f"enabled capability {index} needs rollback notes")
    print(canonical_json({"valid": not errors, "status": "PASS" if not errors else "BLOCKED", "capability_count": len(caps), "errors": errors}), end="")
    return 0 if not errors else 2


def validate_registry_data(registry):
    if not isinstance(registry, dict) or registry.get("schema_version") != 2:
        raise ValueError("capability registry is missing or has unsupported schema_version")
    order = registry.get("selection_order")
    if not isinstance(order, list) or not order or order[0] != "policy" or any(not isinstance(x, str) for x in order) or len(order) != len(set(order)):
        raise ValueError("capability selection must apply policy first")
    capabilities = registry.get("capabilities")
    if not isinstance(capabilities, list):
        raise ValueError("capabilities must be a list")
    mapping = {}
    valid_lifecycle = {"discovered", "reviewed", "installed", "authenticated", "tested", "enabled"}
    for cap in capabilities:
        if not isinstance(cap, dict) or not isinstance(cap.get("id"), str) or not cap["id"].strip():
            raise ValueError("every capability requires a nonempty id")
        if cap["id"] in mapping:
            raise ValueError(f"duplicate capability id: {cap['id']}")
        if cap.get("policy") not in {"allow", "deny", "review"}:
            raise ValueError(f"capability {cap['id']} requires policy allow, deny, or review")
        if cap.get("lifecycle") not in valid_lifecycle:
            raise ValueError(f"capability {cap['id']} has invalid lifecycle")
        for field in ("evidence", "health_check", "license_status", "telemetry_status"):
            if not isinstance(cap.get(field), str) or not cap[field].strip():
                raise ValueError(f"capability {cap['id']} requires nonempty {field}")
        if cap.get("lifecycle") in {"installed", "authenticated", "tested", "enabled"} and not cap.get("version_or_pin"):
            raise ValueError(f"capability {cap['id']} requires version_or_pin at lifecycle {cap['lifecycle']}")
        if cap.get("lifecycle") == "enabled" and not cap.get("rollback"):
            raise ValueError(f"enabled capability {cap['id']} requires rollback notes")
        mapping[cap["id"]] = cap
    return mapping


def load_trusted_acceptance_pin(root: Path, pin_arg: str, accepted=None):
    """Read an operator/reviewer-governed hash pin outside the project.

    The CLI cannot enforce ownership of the external trust anchor. Its custody
    must be separate from the project author for the author-separation claim.
    """
    if not pin_arg:
        raise ValueError("an externally governed --baseline-pin is required")
    pin_path = Path(pin_arg).expanduser()
    if not pin_path.is_absolute():
        pin_path = Path.cwd() / pin_path
    if pin_path.is_symlink() or not pin_path.is_file():
        raise ValueError("baseline pin must be a regular non-symlink file")
    pin_path = pin_path.resolve(strict=True)
    try:
        pin_path.relative_to(root.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("baseline pin must be outside the project directory")
    pin = load_json(pin_path)
    if not isinstance(pin, dict) or pin.get("schema_version") != 1 or not isinstance(pin.get("acceptance_tests"), dict) or not pin["acceptance_tests"]:
        raise ValueError("baseline pin needs schema_version 1 and a nonempty acceptance_tests map")
    if accepted is not None and pin["acceptance_tests"] != accepted:
        raise ValueError("acceptance files do not match the externally governed baseline pin")
    pin_digest = hashlib.sha256(canonical_json(pin["acceptance_tests"]).encode("utf-8")).hexdigest()
    return pin_path, pin, pin_digest


def append_baseline_history(root: Path, run_id: str, baseline_path: Path) -> str:
    history = proof_root(root) / "baselines" / "history.jsonl"
    if history.is_symlink():
        raise ValueError("baseline history must not be a symlink")
    previous = "0" * 64
    if history.exists():
        for line in history.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            entry_hash = row.pop("entry_sha256", None)
            if row.get("previous_sha256") != previous or entry_hash != hashlib.sha256(canonical_json(row).encode()).hexdigest():
                raise ValueError("acceptance baseline history is invalid or was edited")
            recorded_baseline = proof_root(root) / "baselines" / f"{row.get('run_id')}.json"
            if recorded_baseline.is_symlink() or not recorded_baseline.is_file() or sha256(recorded_baseline) != row.get("baseline_sha256"):
                raise ValueError(f"acceptance baseline history points to a missing or changed file: {row.get('run_id')}")
            previous = entry_hash
    row = {"run_id": run_id, "baseline_sha256": sha256(baseline_path), "previous_sha256": previous}
    row["entry_sha256"] = hashlib.sha256(canonical_json(row).encode()).hexdigest()
    fd = os.open(history, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        history.chmod(0o600)
    except OSError:
        pass
    return row["baseline_sha256"]


def cmd_start(args):
    return _start_run(args)


def _start_run(args, *, lock_held=False, recheck=None):
    root = project_root(args.root)
    base = proof_root(root)
    if not base.is_dir():
        raise ValueError("Proof Loop is not initialized; run `proofloop init <project>` first")
    if base.is_symlink():
        raise ValueError(".proof must not be a symlink")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = args.run_id or f"run-{timestamp}"
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("run id must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}")
    if not args.goal.strip() or not args.scope.strip() or not args.done or any(not x.strip() for x in args.done):
        raise ValueError("goal, scope, and nonempty done criteria are required")
    if not args.check or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", x) for x in args.check) or len(set(args.check)) != len(args.check):
        raise ValueError("checks must be unique names matching [A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
    inputs = snapshot(root, args.input)
    accepted = snapshot(root, args.acceptance_test)
    if not accepted:
        raise ValueError("at least one acceptance-test file is required")
    if args.advisory and args.baseline_pin:
        raise ValueError("choose either --advisory or --baseline-pin, not both")
    if not args.advisory and not args.baseline_pin:
        raise ValueError("acceptance-required runs need an externally governed --baseline-pin")
    pin_hash = None
    if not args.advisory:
        _, _, pin_hash = load_trusted_acceptance_pin(root, args.baseline_pin, accepted)
    registry_path = safe_project_file(root, ".proof/capabilities.json")
    registry_hash = sha256(registry_path)
    registry = validate_registry_data(load_json(registry_path))
    selected, excluded = list(args.use_capability), list(args.exclude_capability)
    if len(set(selected)) != len(selected) or len(set(excluded)) != len(excluded) or set(selected) & set(excluded):
        raise ValueError("capability selections must be unique and cannot be both selected and excluded")
    decisions = []
    for cap_id in selected + excluded:
        if cap_id not in registry:
            raise ValueError(f"unknown capability: {cap_id}")
        cap = registry[cap_id]
        eligible = cap.get("policy") == "allow" and cap.get("lifecycle") in {"tested", "enabled"}
        if cap_id in selected and not eligible:
            raise ValueError(f"capability {cap_id} is not selectable after policy/lifecycle filtering (policy={cap.get('policy')}, lifecycle={cap.get('lifecycle')})")
        decisions.append({"id": cap_id, "decision": "selected" if cap_id in selected else "excluded", "policy": cap.get("policy"), "lifecycle": cap.get("lifecycle"), "reason": "policy allow and lifecycle tested/enabled" if cap_id in selected else "explicitly excluded by Run Card"})

    with (nullcontext() if lock_held else project_lock(root)):
        runs = base / "runs"
        if runs.is_symlink():
            raise ValueError(".proof/runs must not be a symlink")
        runs.mkdir(parents=True, exist_ok=True)
        dest = runs / run_id
        if dest.exists() or dest.is_symlink():
            raise ValueError(f"run already exists: {run_id}")
        if inputs != snapshot(root, args.input) or accepted != snapshot(root, args.acceptance_test):
            raise ValueError("input changed while preparing the run; retry with a new baseline")
        if sha256(registry_path) != registry_hash:
            raise ValueError("capability policy changed while preparing the run; retry after reviewing the registry")
        baselines = base / "baselines"
        if baselines.is_symlink():
            raise ValueError(".proof/baselines must not be a symlink")
        baselines.mkdir(mode=0o700, exist_ok=True)
        baseline_path = baselines / f"{run_id}.json"
        if baseline_path.exists() or baseline_path.is_symlink():
            raise ValueError(f"acceptance baseline already exists: {run_id}")
        stage = Path(tempfile.mkdtemp(prefix=f".staging-{run_id}-", dir=runs))
        try:
            manifest_data = {"schema_version": 3, "created_at": now(), "inputs": inputs, "acceptance_tests": accepted}
            baseline = {"schema_version": 1, "run_id": run_id, "created_at": now(), "acceptance_tests": accepted, "protection": "exclusive-create, read-only mode, append-only hash-chained history; same-user edits remain possible"}
            write_json(stage / "input-manifest.json", manifest_data, exclusive=True)
            card = {
                "schema_version": 3, "run_id": run_id, "created_at": now(), "goal": args.goal,
                "scope": args.scope, "playbook": args.playbook, "risk": args.risk,
                "capabilities_selected": selected, "capabilities_excluded": excluded, "capability_decisions": decisions,
                "selection_order_applied": ["policy", "lifecycle"],
                "selection_metadata_not_evaluated": ["fit/use_when", "not_for", "health_check", "ranking"],
                "capability_selection_note": "Explicit selections are filtered by policy and lifecycle only; descriptive fit/health/ranking fields are not evaluated or executed.",
                "baseline_pin_sha256": pin_hash,
                "acceptance_mode": "advisory" if args.advisory else "acceptance_required",
                "assumptions": args.assumption, "done_when": args.done,
                "human_decision_required": args.risk in {"high", "critical"},
                "input_files": list(inputs), "acceptance_test_files": list(accepted),
                "required_checks": args.check, "repair_rounds_allowed": 2,
                "worker_limit": 3, "verification_mode": "independent" if args.risk in {"medium", "high", "critical"} else "fast_checks_with_independent_review_if_scope_requires",
            }
            if recheck is not None:
                card["recheck_parent"] = recheck["run_id"]
                card["recheck_parent_card_sha256"] = recheck["card_sha256"]
            write_json(stage / "run-card.json", card, exclusive=True)
            baseline_hash = hashlib.sha256(canonical_json(baseline).encode()).hexdigest()
            receipt = {
                "schema_version": 3, "run_id": run_id, "created_at": now(), "run_state": "in_progress",
                "input_manifest_sha256": sha256(stage / "input-manifest.json"),
                "acceptance_tests_sha256": hashlib.sha256(canonical_json(accepted).encode()).hexdigest(),
                "acceptance_baseline_sha256": baseline_hash,
                "baseline_pin_sha256": pin_hash,
                "acceptance_mode": "advisory" if args.advisory else "acceptance_required",
                "checks": {},
                "independent_review": {"status": "not_run", "reviewer": "", "fresh_context": False, "notes": ""},
                "claims": [], "coverage": {"checked": [], "not_checked": []}, "exceptions": [],
                "outputs": {}, "human_decision": "pending" if card["human_decision_required"] else "not_required",
                "repair_rounds_used": 0, "checker_version": VERSION, "revision": 0,
                "environment": {"python": platform.python_version(), "platform": platform.platform()},
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "execution_observations": {"cli_invocations": None, "spend": None, "note": "Unknown until measured; do not invent observations."},
            }
            if recheck is not None:
                receipt["repair_rounds_used"] = recheck["repair_rounds_used"]
            write_json(stage / "receipt.json", receipt, exclusive=True)
            write_json(baseline_path, baseline, exclusive=True, mode=0o400)
            history_baseline_hash = append_baseline_history(root, run_id, baseline_path)
            if history_baseline_hash != sha256(baseline_path):
                raise ValueError("baseline history hash did not match stored baseline")
            os.rename(stage, dest)
            append_event(root, run_id, "started", run_card=f"{PROOF_DIR}/runs/{run_id}/run-card.json", input_count=len(inputs), acceptance_baseline_sha256=history_baseline_hash, run_card_sha256=sha256(dest / "run-card.json"), recheck_parent=card.get("recheck_parent"), repair_rounds_used=receipt["repair_rounds_used"])
            print(canonical_json(card), end="")
            return 0
        except Exception:
            if stage.exists():
                import shutil
                shutil.rmtree(stage, ignore_errors=True)
            raise


def cmd_recheck(args):
    """Freeze the current candidate into a new run without refreshing criteria."""
    root = project_root(args.root)
    with project_lock(root):
        parent = run_dir(root, args.run_id)
        card_path = parent / "run-card.json"
        card, receipt = load_json(card_path), load_json(parent / "receipt.json")
        manifest_path = parent / "input-manifest.json"
        manifest = load_json(manifest_path)
        if not all(isinstance(v, dict) for v in (card, receipt, manifest)):
            raise ValueError("parent run files must be objects")
        if card.get("run_id") != args.run_id or receipt.get("run_id") != args.run_id:
            raise ValueError("parent run identity mismatch")
        if receipt.get("run_state") not in {"in_progress", "failed", "blocked", "passed_for_scope"}:
            raise ValueError("parent run is cancelled, superseded, or uncertain")
        rows = events(root)
        if any(row.get("recheck_parent") == args.run_id for row in rows):
            raise ValueError("parent already has a recheck; continue from its child")
        starts = [r for r in rows if r.get("run_id") == args.run_id and r.get("event") == "started"]
        if len(starts) != 1:
            raise ValueError("parent start attribution is missing or ambiguous")
        if starts[0].get("run_card_sha256") != sha256(card_path):
            raise ValueError("parent card changed or predates recheck binding; inspect and start a separately reviewed run")
        if receipt.get("input_manifest_sha256") != sha256(manifest_path):
            raise ValueError("parent input manifest changed")
        baseline, errors = baseline_history_state(root, args.run_id)
        if errors or baseline is None:
            raise ValueError("parent acceptance baseline is invalid: " + "; ".join(errors))
        accepted = baseline["acceptance_tests"]
        if manifest.get("acceptance_tests") != accepted or snapshot(root, list(accepted)) != accepted:
            raise ValueError("frozen acceptance tests changed; recheck cannot redefine success")
        if card.get("input_files") != list(manifest.get("inputs", {})) or card.get("acceptance_test_files") != list(accepted):
            raise ValueError("parent card/manifest paths disagree")
        mode = card.get("acceptance_mode")
        if mode not in {"advisory", "acceptance_required"} or receipt.get("acceptance_mode") != mode:
            raise ValueError("parent acceptance mode mismatch")
        if mode == "advisory" and args.baseline_pin:
            raise ValueError("an advisory recheck cannot acquire acceptance mode")
        if mode == "acceptance_required":
            _, pin, digest = load_trusted_acceptance_pin(root, args.baseline_pin, accepted)
            if digest != card.get("baseline_pin_sha256") or pin.get("approved_run_card_sha256") != sha256(card_path):
                raise ValueError("parent requires its original reviewer-approved pin")
        used = receipt.get("repair_rounds_used")
        budget_values = [starts[0].get("repair_rounds_used", 0)] + [r.get("round", 0) for r in rows if r.get("run_id") == args.run_id and r.get("event") == "repair_started"]
        if any(type(v) is not int or not 0 <= v <= 2 for v in budget_values):
            raise ValueError("parent repair ledger is malformed")
        observed = max(budget_values)
        if type(used) is not int or not 0 <= used <= 2 or used != observed:
            raise ValueError("parent repair budget is invalid or disagrees with ledger")
        fresh = argparse.Namespace(root=str(root), run_id=args.new_run_id,
            goal=card["goal"], scope=card["scope"], playbook=card["playbook"], risk=card["risk"],
            done=card["done_when"], check=card["required_checks"], input=card["input_files"],
            acceptance_test=list(accepted), advisory=mode == "advisory", baseline_pin=args.baseline_pin,
            use_capability=card["capabilities_selected"], exclude_capability=card["capabilities_excluded"],
            assumption=card["assumptions"])
        return _start_run(fresh, lock_held=True, recheck={"run_id": args.run_id,
            "card_sha256": sha256(card_path), "repair_rounds_used": used})


def baseline_history_state(root: Path, run_id: str):
    errors = []
    base = proof_root(root) / "baselines"
    history = base / "history.jsonl"
    if base.is_symlink() or history.is_symlink():
        return None, ["baseline directory/history must not be symlinks"]
    if not history.is_file():
        return None, ["acceptance baseline history is missing"]
    previous = "0" * 64
    entries = {}
    try:
        for number, line in enumerate(history.read_text(encoding="utf-8").splitlines(), start=1):
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"history line {number} is not an object")
            row = dict(row)
            entry_hash = row.pop("entry_sha256", None)
            if row.get("previous_sha256") != previous or entry_hash != hashlib.sha256(canonical_json(row).encode()).hexdigest():
                raise ValueError(f"baseline history hash chain failed at line {number}")
            if row.get("run_id") in entries:
                raise ValueError(f"duplicate baseline history entry for {row.get('run_id')}")
            entries[row.get("run_id")] = row.get("baseline_sha256")
            archived_baseline = base / f"{row.get('run_id')}.json"
            if archived_baseline.is_symlink() or not archived_baseline.is_file() or sha256(archived_baseline) != row.get("baseline_sha256"):
                raise ValueError(f"baseline file missing or changed for history entry {row.get('run_id')}")
            previous = entry_hash
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return None, [f"acceptance baseline history is invalid: {exc}"]
    expected = entries.get(run_id)
    baseline_path = base / f"{run_id}.json"
    if not expected or baseline_path.is_symlink() or not baseline_path.is_file():
        return None, [f"acceptance baseline missing from protected history: {run_id}"]
    actual = sha256(baseline_path)
    if actual != expected:
        errors.append("acceptance baseline file hash differs from append-only history")
    try:
        baseline = load_json(baseline_path)
        if baseline.get("run_id") != run_id or not isinstance(baseline.get("acceptance_tests"), dict):
            errors.append("acceptance baseline record is malformed")
    except (OSError, json.JSONDecodeError, AttributeError) as exc:
        baseline = None
        errors.append(f"acceptance baseline record is unreadable: {exc}")
    return baseline, errors


def current_input_state(root: Path, run_id: str):
    rdir = run_dir(root, run_id)
    recorded = load_json(rdir / "input-manifest.json")
    # Labels are for display only. Never merge namespaces or strip a display
    # prefix from a real filename: inputs may literally start with that prefix.
    manifest = {kind: recorded.get(kind, {}) for kind in ("inputs", "acceptance_tests")}
    now_state = {kind: {} for kind in manifest}
    errors, changed = [], []
    baseline, baseline_errors = baseline_history_state(root, run_id)
    errors.extend(baseline_errors)
    if baseline is not None and recorded.get("acceptance_tests") != baseline.get("acceptance_tests"):
        errors.append("run acceptance manifest differs from the separately recorded baseline")
    for kind, entries in manifest.items():
        for relative, previous in entries.items():
            label = f"[acceptance-test] {relative}" if kind == "acceptance_tests" else relative
            try:
                path = safe_project_file(root, relative)
                now_state[kind][relative] = {"sha256": sha256(path), "bytes": path.stat().st_size}
            except (ValueError, OSError) as exc:
                errors.append(str(exc))
            if now_state[kind].get(relative) != previous and label not in changed:
                changed.append(label)
    if baseline is not None:
        for relative, prior in baseline.get("acceptance_tests", {}).items():
            actual = now_state["acceptance_tests"].get(relative)
            if actual != prior:
                if f"[acceptance-test] {relative}" not in changed:
                    changed.append(f"[acceptance-test] {relative}")
    return {"stale": bool(changed or errors), "changed": changed, "errors": errors, "current": now_state}


def _cmd_status_locked(args):
    root = project_root(args.root)
    selected = args.run_id
    run_events = events(root, selected)
    if not run_events:
        raise ValueError(f"no ledger events for run: {selected}")
    rdir = run_dir(root, selected)
    receipt = load_json(rdir / "receipt.json") if (rdir / "receipt.json").exists() else {}
    freshness = current_input_state(root, selected)
    append_event(root, selected, "status_checked", stale=freshness["stale"])
    print(canonical_json({"run_id": selected, "last_event": run_events[-1], "repair_rounds_used": receipt.get("repair_rounds_used", 0), "run_state": receipt.get("run_state", "missing"), "input_fresh": not freshness["stale"], "changed_inputs": freshness["changed"], "missing_inputs": freshness["errors"]}), end="")
    return 0


def cmd_status(args):
    root = project_root(args.root)
    with project_lock(root):
        return _cmd_status_locked(args)


def _cmd_resume_locked(args):
    root = project_root(args.root)
    selected = args.run_id
    run_events = events(root, selected)
    if not run_events:
        raise ValueError(f"no ledger events for run: {selected}")
    freshness = current_input_state(root, selected)
    receipt = load_json(run_dir(root, selected) / "receipt.json")
    result = receipt.get("run_state")
    if freshness["stale"]:
        next_action = "use proofloop recheck with a new run ID, rerun checks, then complete the new receipt; keep the original evidence"
    elif result == "in_progress":
        next_action = "complete the receipt, then run proofloop validate"
    elif result in {"failed", "blocked"}:
        next_action = "inspect the blocker; use at most two repair rounds, then record a decision"
    elif result == "passed_for_scope":
        next_action = "review the receipt and decide whether to accept the deliverable"
    else:
        next_action = "inspect the run artifacts and record the next step"
    append_event(root, selected, "resumed", stale=freshness["stale"], next_action=next_action)
    print(canonical_json({"run_id": selected, "last_event": run_events[-1], "stale": freshness["stale"], "changed_inputs": freshness["changed"], "run_state": result, "repair_rounds_used": receipt.get("repair_rounds_used", 0), "next_action": next_action}), end="")
    return 0


def cmd_resume(args):
    root = project_root(args.root)
    with project_lock(root):
        return _cmd_resume_locked(args)


def cmd_close(args):
    root = project_root(args.root)
    rdir = run_dir(root, args.run_id)
    receipt_path = rdir / "receipt.json"
    expected = sha256(receipt_path)
    receipt = load_json(receipt_path)
    with project_lock(root):
        if sha256(receipt_path) != expected:
            raise ValueError("stale receipt; concurrent write changed this run")
        receipt["run_state"] = args.state
        receipt["revision"] = int(receipt.get("revision", 0)) + 1
        receipt.setdefault("exceptions", []).append({"type": "run_closed", "reason": args.reason})
        write_json(receipt_path, receipt, expected_sha256=expected)
        append_event(root, args.run_id, args.state, reason=args.reason, revision=receipt["revision"])
    print(f"Run {args.run_id} recorded as {args.state}")
    return 0


def cmd_observe(args):
    root = project_root(args.root)
    rdir = run_dir(root, args.run_id)
    receipt_path = rdir / "receipt.json"
    expected = sha256(receipt_path)
    receipt = load_json(receipt_path)
    if args.invocations < 0 or not args.source.strip():
        raise ValueError("invocations must be nonnegative and observation source nonempty")
    observations = receipt.setdefault("execution_observations", {})
    observations["invocations"] = args.invocations
    observations["spend"] = args.spend
    observations["source"] = args.source
    observations["observed_at"] = now()
    observations["self_reported"] = True
    receipt["revision"] = int(receipt.get("revision", 0)) + 1
    with project_lock(root):
        if sha256(receipt_path) != expected:
            raise ValueError("stale receipt; concurrent write changed this run")
        write_json(receipt_path, receipt, expected_sha256=expected)
        append_event(root, args.run_id, "execution_observation_recorded", invocations=args.invocations, spend=args.spend, source=args.source, revision=receipt["revision"])
    print("Observed execution metrics recorded as self-reported; this command does not enforce limits.")
    return 0


def cmd_drift(args):
    root = project_root(args.root)
    run_dir(root, args.run_id)
    card = load_json(run_dir(root, args.run_id) / "run-card.json")
    if "source-view-drift" not in card.get("required_checks", []):
        raise ValueError("source-view-drift must be declared as a required check on the Run Card")
    manifest = load_json(run_dir(root, args.run_id) / "input-manifest.json")
    declared_inputs = manifest.get("inputs", {})
    if args.source not in declared_inputs or args.view not in declared_inputs:
        raise ValueError("drift source and view must both be declared Run Card inputs")
    source_path = safe_project_file(root, args.source)
    view_path = safe_project_file(root, args.view)
    source = load_json(source_path)
    items = source.get("items")
    if not isinstance(items, list) or any(not isinstance(item, dict) or "id" not in item for item in items):
        raise ValueError("source must contain an items array with an id for each record")
    expected = {"record_count": len(items), "ids": [item["id"] for item in items], "fixture_classification": source.get("fixture_classification", "synthetic")}
    actual = load_json(view_path)
    drift = not json_equal(actual, expected)
    exit_code = 1 if drift else 0
    report = {
        "schema_version": 2, "runner": "proofloop-drift", "runner_version": VERSION,
        "exit_code": exit_code, "run_id": args.run_id,
        "check": "source-view-drift", "state": "fail" if drift else "pass",
        "reason": "generated view differs from the declared source projection" if drift else "generated view matches the declared source projection",
        "source": args.source, "source_sha256": sha256(source_path),
        "view": args.view, "view_sha256": sha256(view_path),
        "expected": expected, "actual": actual,
        "coverage": ["record_count", "ordered_ids", "fixture_classification"], "checked_at": now(),
    }
    with project_lock(root):
        if source_path.is_symlink() or view_path.is_symlink() or sha256(source_path) != report["source_sha256"] or sha256(view_path) != report["view_sha256"]:
            raise ValueError("source/view changed while drift check was running")
        log_path = safe_write_path(root, args.log)
        write_json(log_path, report, exclusive=True)
        append_event(root, args.run_id, "check_recorded", check="source-view-drift", state=report["state"], log=args.log)
    print(canonical_json(report), end="")
    return exit_code


def safe_write_path(root: Path, relative: str) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or not relative or any(part == ".." for part in raw.parts):
        raise ValueError(f"output must be project-relative: {relative}")
    cursor = root
    for part in raw.parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"output path contains a symlink: {relative}")
    candidate = root / raw
    if candidate.is_symlink():
        raise ValueError(f"output path is a symlink: {relative}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"output escapes project root: {relative}") from exc
    if resolved.exists():
        raise ValueError(f"refusing to overwrite existing evidence/output file: {relative}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def cmd_repair(args):
    root = project_root(args.root)
    rdir = run_dir(root, args.run_id)
    receipt_path = rdir / "receipt.json"
    expected = sha256(receipt_path)
    receipt = load_json(receipt_path)
    if any(row.get("recheck_parent") == args.run_id for row in events(root)):
        raise ValueError("run already has a recheck; repair its child instead")
    used = int(receipt.get("repair_rounds_used", 0))
    if used >= 2:
        receipt["run_state"] = "blocked"
        receipt.setdefault("exceptions", []).append({"type": "repair_limit", "detail": "Two repair rounds were already used; further repair requires an explicit human decision."})
        receipt["revision"] = int(receipt.get("revision", 0)) + 1
        with project_lock(root):
            if any(row.get("recheck_parent") == args.run_id for row in events(root)):
                raise ValueError("run already has a recheck; repair its child instead")
            if sha256(receipt_path) != expected:
                raise ValueError("stale receipt; concurrent write changed this run")
            write_json(receipt_path, receipt, expected_sha256=expected)
            append_event(root, args.run_id, "blocked", reason="repair_limit", repair_rounds_used=used, revision=receipt["revision"])
        print("BLOCKED: repair limit reached (2 rounds); no third repair was started.", file=sys.stderr)
        return 3
    used += 1
    receipt["repair_rounds_used"] = used
    receipt["run_state"] = "in_progress"
    receipt["revision"] = int(receipt.get("revision", 0)) + 1
    with project_lock(root):
        if any(row.get("recheck_parent") == args.run_id for row in events(root)):
            raise ValueError("run already has a recheck; repair its child instead")
        if sha256(receipt_path) != expected:
            raise ValueError("stale receipt; concurrent write changed this run")
        write_json(receipt_path, receipt, expected_sha256=expected)
        append_event(root, args.run_id, "repair_started", round=used, reason=args.reason, revision=receipt["revision"])
    print(f"Repair round {used}/2 recorded for {args.run_id}")
    return 0


def verify_drift_log(root: Path, run_id: str, name: str, log_path: Path, log_data: dict):
    if name != "source-view-drift" or log_data.get("runner") != "proofloop-drift":
        raise ValueError("unrecognized runner/check pair; only built-in proofloop-drift currently produces Verified pass evidence")
    if log_data.get("run_id") != run_id or log_data.get("check") != name:
        raise ValueError("runner log run/check attribution does not match")
    if log_data.get("schema_version") != 2 or log_data.get("runner_version") != VERSION:
        raise ValueError("drift runner schema/version does not match the current checker; rerun drift")
    source = safe_project_file(root, log_data.get("source", ""))
    view = safe_project_file(root, log_data.get("view", ""))
    manifest = load_json(run_dir(root, run_id) / "input-manifest.json")
    declared_inputs = manifest.get("inputs", {})
    if log_data.get("source") not in declared_inputs or log_data.get("view") not in declared_inputs:
        raise ValueError("drift evidence source/view are not declared Run Card inputs")
    source_hash, view_hash = sha256(source), sha256(view)
    if source_hash != log_data.get("source_sha256") or view_hash != log_data.get("view_sha256"):
        raise ValueError("drift evidence source/view hashes are stale")
    source_data, view_data = load_json(source), load_json(view)
    items = source_data.get("items")
    if not isinstance(items, list) or any(not isinstance(item, dict) or "id" not in item for item in items):
        raise ValueError("drift source does not satisfy the built-in runner input contract")
    computed_expected = {"record_count": len(items), "ids": [item["id"] for item in items], "fixture_classification": source_data.get("fixture_classification", "synthetic")}
    state = "pass" if json_equal(view_data, computed_expected) else "fail"
    exit_code = 1 if state == "fail" else 0
    reason = "generated view differs from the declared source projection" if state == "fail" else "generated view matches the declared source projection"
    if not json_equal(log_data.get("expected"), computed_expected) or not json_equal(log_data.get("actual"), view_data) or log_data.get("state") != state or log_data.get("reason") != reason:
        raise ValueError("drift runner log does not match recomputed source/view results")
    if type(log_data.get("exit_code")) is not int or log_data["exit_code"] != exit_code:
        raise ValueError("drift runner exit_code does not match recomputed outcome")
    if state != "pass" or exit_code != 0:
        raise ValueError("drift runner recomputed fail (exit_code 1); cannot support a passing check")
    if log_data.get("coverage") != ["record_count", "ordered_ids", "fixture_classification"]:
        raise ValueError("drift runner coverage declaration is missing or inconsistent")
    if not isinstance(log_data.get("checked_at"), str) or not log_data["checked_at"].strip():
        raise ValueError("runner log timestamp is required")
    return set(log_data["coverage"])


def validate_receipt(root: Path, run_id: str, baseline_pin_arg: str):
    rdir = run_dir(root, run_id)
    path = rdir / "receipt.json"
    receipt = load_json(path)
    errors = []
    pin_hash = None
    try:
        card_for_pin = load_json(rdir / "run-card.json")
        manifest_for_pin = load_json(rdir / "input-manifest.json")
        if card_for_pin.get("acceptance_mode") == "advisory":
            if receipt.get("run_state") == "passed_for_scope":
                errors.append("advisory runs cannot be accepted as passed_for_scope")
            if baseline_pin_arg or card_for_pin.get("baseline_pin_sha256") is not None or receipt.get("baseline_pin_sha256") is not None:
                errors.append("advisory runs must not claim an acceptance baseline pin")
        else:
            _, pin, pin_hash = load_trusted_acceptance_pin(root, baseline_pin_arg, manifest_for_pin.get("acceptance_tests", {}))
            if card_for_pin.get("baseline_pin_sha256") != pin_hash or receipt.get("baseline_pin_sha256") != pin_hash:
                errors.append("external acceptance baseline pin hash does not match Run Card and receipt")
            approved_card_hash = pin.get("approved_run_card_sha256")
            current_card_hash = sha256(rdir / "run-card.json")
            if not isinstance(approved_card_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", approved_card_hash):
                errors.append("external pin lacks reviewer approval of the Run Card hash")
            elif approved_card_hash != current_card_hash:
                errors.append("Run Card changed after external reviewer approval")
    except Exception as exc:
        errors.append(f"external acceptance baseline pin validation failed: {exc}")
    if not isinstance(receipt, dict):
        return ["receipt must be a JSON object"], current_input_state(root, run_id)
    required = ["schema_version", "run_id", "run_state", "created_at", "input_manifest_sha256", "acceptance_tests_sha256", "acceptance_baseline_sha256", "baseline_pin_sha256", "checks", "independent_review", "claims", "coverage", "exceptions", "outputs", "human_decision", "repair_rounds_used", "revision", "checker_version", "environment", "expires_at", "execution_observations"]
    missing = [field for field in required if field not in receipt]
    if missing:
        errors.append("missing fields: " + ", ".join(missing))
    if receipt.get("run_id") != run_id:
        errors.append("receipt run_id does not match requested run")
    if receipt.get("schema_version") != 3:
        errors.append("schema_version must be 3")
    if receipt.get("run_state") != "passed_for_scope":
        errors.append("run_state must be passed_for_scope for an accepted receipt")
    allowed_run_states = {"in_progress", "passed_for_scope", "failed", "blocked", "cancelled", "superseded"}
    if not isinstance(receipt.get("run_state"), str) or receipt["run_state"] not in allowed_run_states:
        errors.append("run_state is not a supported state")
    if type(receipt.get("repair_rounds_used")) is not int or not 0 <= receipt.get("repair_rounds_used", -1) <= 2:
        errors.append("repair_rounds_used must be an integer from 0 through 2")
    if type(receipt.get("revision")) is not int or receipt.get("revision", -1) < 0:
        errors.append("revision must be a nonnegative integer")
    if receipt.get("checker_version") != VERSION:
        errors.append(f"checker_version must match the executing CLI version {VERSION}")
    environment = receipt.get("environment")
    if not isinstance(environment, dict) or not all(isinstance(environment.get(k), str) and environment[k].strip() for k in ("python", "platform")):
        errors.append("environment must include nonempty python and platform strings")
    elif environment.get("python") != platform.python_version() or environment.get("platform") != platform.platform():
        errors.append("environment metadata does not match the current validator environment")
    if not isinstance(receipt.get("human_decision"), str) or receipt["human_decision"] not in {"not_required", "approved", "pending", "rejected"}:
        errors.append("human_decision must be a supported state")
    coverage = receipt.get("coverage", {})
    if not isinstance(coverage, dict) or not isinstance(coverage.get("checked"), list) or not isinstance(coverage.get("not_checked"), list):
        errors.append("coverage must contain checked and not_checked lists")
        coverage = {"checked": [], "not_checked": []}
    if not isinstance(receipt.get("exceptions"), list):
        errors.append("exceptions must be a list")
    try:
        card = load_json(rdir / "run-card.json")
        if not isinstance(card, dict):
            raise ValueError("not a JSON object")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"run-card.json is missing or unreadable: {exc}")
        card = {}
    check_records = receipt.get("checks", {})
    if not isinstance(check_records, dict):
        errors.append("checks must be an object keyed by required check name")
        check_records = {}
    validated_checks = {}
    observed_coverage = set()
    for name in card.get("required_checks", []):
        check = check_records.get(name)
        if not isinstance(check, dict):
            errors.append(f"required check is missing: {name}")
            continue
        state = check.get("state")
        if not isinstance(state, str) or state not in {"pass", "fail", "blocked", "not_run", "not_applicable"}:
            errors.append(f"check {name} has an invalid state")
            continue
        if not isinstance(check.get("reason"), str) or not check["reason"].strip():
            errors.append(f"check {name} requires a nonempty reason")
        if state in {"fail", "blocked", "not_run"}:
            errors.append(f"check {name} is {state}: {check.get('reason') or 'reason not supplied'}")
        if state == "not_applicable" and not check.get("reason"):
            errors.append(f"check {name} is not_applicable without a reason")
        if state == "pass":
            log = check.get("log")
            if not isinstance(log, dict) or not all(isinstance(log.get(k), str) and log[k].strip() for k in ("path", "sha256", "runner")):
                errors.append(f"check {name} lacks a valid path/hash/runner attribution")
                continue
            try:
                log_file = safe_project_file(root, log["path"])
                if sha256(log_file) != log["sha256"]:
                    raise ValueError("check log changed after evidence capture")
                log_data = load_json(log_file)
                if not isinstance(log_data, dict) or log_data.get("runner") != log["runner"]:
                    raise ValueError("runner attribution does not match the evidence file")
                fields = verify_drift_log(root, run_id, name, log_file, log_data)
                validated_checks[name] = {"state": "pass", "log_path": log["path"], "coverage": fields}
                observed_coverage.update(fields)
            except (OSError, ValueError, json.JSONDecodeError, TypeError, AttributeError) as exc:
                errors.append(f"check {name} evidence is unverified: {exc}")
    if not validated_checks:
        errors.append("at least one required check must pass with evidence from a recognized, recomputable runner")
    review = receipt.get("independent_review", {})
    if not isinstance(review, dict):
        errors.append("independent_review must be an object")
        review = {}
    if card.get("verification_mode") == "independent" and review.get("status") != "pass":
        errors.append("this run's risk tier requires a passing independent review")
    elif not isinstance(review.get("status"), str) or review["status"] not in {"pass", "not_applicable"}:
        errors.append("independent_review must be pass or not_applicable with a reason")
    if review.get("status") == "pass" and not (isinstance(review.get("reviewer"), str) and review["reviewer"].strip()):
        errors.append("reviewer attribution is required; it is an attribution, not proof of independence")
    if review.get("status") == "not_applicable" and not (isinstance(review.get("reason"), str) and review["reason"].strip()):
        errors.append("not_applicable review requires a reason")
    claims = receipt.get("claims", [])
    if not isinstance(claims, list) or not claims:
        errors.append("at least one evidence-labeled claim is required")
    else:
        labels = {"verified", "reported", "inferred", "unknown"}
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict) or not isinstance(claim.get("label"), str) or claim.get("label", "").lower() not in labels:
                errors.append(f"claim {index} has an invalid evidence label")
                continue
            label = claim["label"].lower()
            if not isinstance(claim.get("claim"), str) or not claim["claim"].strip():
                errors.append(f"claim {index} has no claim text")
            if label == "verified":
                check_name = claim.get("check")
                expected_text = f"Check {check_name} passed."
                evidence = claim.get("evidence")
                if claim.get("claim_type") != "check_pass" or not isinstance(check_name, str) or claim.get("claim") != expected_text:
                    errors.append(f"claim {index} Verified text must be the exact structured check-pass statement")
                verified_check = validated_checks.get(check_name) if isinstance(check_name, str) else None
                supports = claim.get("supports_checks")
                if not verified_check or supports != [check_name]:
                    errors.append(f"claim {index} Verified claim must name exactly one validated passing check")
                if evidence != ([verified_check["log_path"]] if verified_check else []):
                    errors.append(f"claim {index} Verified evidence must point to that check's validated runner log")
            if label == "reported" and not claim.get("source"):
                errors.append(f"claim {index} is Reported but has no source")
            if label == "inferred" and not claim.get("basis"):
                errors.append(f"claim {index} is Inferred but has no evidence basis")
            if label == "unknown" and (not claim.get("open_question") or not claim.get("owner")):
                errors.append(f"claim {index} is Unknown but lacks an open question or owner")
    checked = coverage.get("checked", [])
    not_checked = coverage.get("not_checked", [])
    if any(not isinstance(x, str) or not x.strip() for x in checked + not_checked):
        errors.append("coverage entries must be unique nonempty strings")
        checked, not_checked = [], []
    elif len(set(checked)) != len(checked) or len(set(not_checked)) != len(not_checked):
        errors.append("coverage entries must be unique nonempty strings")
    if set(checked) != observed_coverage:
        errors.append("coverage.checked must exactly match coverage reproduced by validated passing runners")
    if set(checked) & set(not_checked):
        errors.append("coverage cannot mark the same item checked and not_checked")
    if card.get("human_decision_required") and receipt.get("human_decision") != "approved":
        errors.append("human decision is required for this risk tier and must be approved")
    try:
        manifest_path = rdir / "input-manifest.json"
        manifest = load_json(manifest_path)
        baseline, baseline_errors = baseline_history_state(root, run_id)
        errors.extend(baseline_errors)
        baseline_path = proof_root(root) / "baselines" / f"{run_id}.json"
        if receipt.get("input_manifest_sha256") != sha256(manifest_path):
            errors.append("receipt input_manifest_sha256 does not match the run manifest")
        if card.get("acceptance_mode") != "advisory" and receipt.get("baseline_pin_sha256") != pin_hash:
            errors.append("receipt does not bind the external acceptance pin")
        if baseline is not None:
            baseline_hash = sha256(baseline_path)
            expected_acceptance_hash = hashlib.sha256(canonical_json(baseline["acceptance_tests"]).encode()).hexdigest()
            if receipt.get("acceptance_baseline_sha256") != baseline_hash:
                errors.append("receipt acceptance baseline hash does not match protected baseline history")
            if receipt.get("acceptance_tests_sha256") != expected_acceptance_hash:
                errors.append("receipt acceptance test hash does not match protected baseline")
            if manifest.get("acceptance_tests") != baseline.get("acceptance_tests"):
                errors.append("run manifest acceptance tests differ from protected baseline")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        errors.append(f"manifest/baseline integrity check failed: {exc}")
    freshness = current_input_state(root, run_id)
    if freshness["stale"]:
        errors.append("receipt is stale: declared inputs or frozen acceptance baseline changed")
    outputs = receipt.get("outputs", {})
    if not isinstance(outputs, dict) or not outputs:
        errors.append("outputs must map at least one project-relative file to its SHA-256")
    else:
        for relative, expected_hash in outputs.items():
            try:
                actual = sha256(safe_project_file(root, relative))
                if actual != expected_hash:
                    errors.append(f"output hash mismatch: {relative}")
            except (TypeError, ValueError, OSError) as exc:
                errors.append(f"output is not a readable project file: {relative} ({exc})")
    try:
        expires = datetime.fromisoformat(receipt["expires_at"].replace("Z", "+00:00"))
        if expires <= datetime.now(timezone.utc):
            errors.append("receipt has expired; rerun relevant checks and create a fresh receipt")
    except (KeyError, ValueError, TypeError, AttributeError):
        errors.append("expires_at must be an ISO-8601 timestamp")
    return errors, freshness


def cmd_validate(args):
    root = project_root(args.root)
    with project_lock(root):
        errors, freshness = validate_receipt(root, args.run_id, args.baseline_pin)
        report = {"run_id": args.run_id, "valid": not errors, "status": "PASS" if not errors else "BLOCKED", "errors": errors, "input_fresh": not freshness["stale"], "changed_inputs": freshness["changed"], "note": "Known built-in runner evidence is recomputed against current inputs. Runner labels from external tools are rejected as unverified; local same-user files are not cryptographic attestations."}
        print(canonical_json(report), end="")
        append_event(root, args.run_id, "receipt_validated", valid=not errors, error_count=len(errors))
        return 0 if not errors else 2


def parser():
    p = argparse.ArgumentParser(prog="proofloop", description="Portable, project-scoped evidence workflow")
    p.add_argument("--version", action="version", version=f"Proof Loop {VERSION}")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="create project-scoped .proof starter files")
    init.add_argument("root"); init.add_argument("--force", action="store_true", help="fill missing starter files without overwriting existing files")
    init.set_defaults(func=cmd_init)
    registry = sub.add_parser("validate-registry", help="check capability registry lifecycle and policy-first fields")
    registry.add_argument("--root", required=True); registry.set_defaults(func=cmd_validate_registry)
    start = sub.add_parser("start", help="create an intake Run Card and run state")
    start.add_argument("--root", required=True); start.add_argument("--goal", required=True); start.add_argument("--scope", required=True)
    start.add_argument("--playbook", default="build"); start.add_argument("--risk", choices=["low", "medium", "high", "critical"], default="low")
    start.add_argument("--use-capability", action="append", default=[]); start.add_argument("--exclude-capability", action="append", default=[])
    start.add_argument("--assumption", action="append", default=[]); start.add_argument("--done", action="append", required=True)
    start.add_argument("--check", action="append", required=True, help="required check name; repeat for each gate check")
    start.add_argument("--input", action="append", default=[]); start.add_argument("--acceptance-test", action="append", required=True); mode = start.add_mutually_exclusive_group(); mode.add_argument("--baseline-pin", help="externally governed acceptance hash pin outside the project"); mode.add_argument("--advisory", action="store_true", help="allow low-risk work without acceptance pin; the run can never be accepted as passed_for_scope"); start.add_argument("--run-id")
    start.set_defaults(func=cmd_start)
    recheck = sub.add_parser("recheck", help="freeze repaired inputs in a new run with unchanged acceptance criteria")
    recheck.add_argument("--root", required=True); recheck.add_argument("--run-id", required=True)
    recheck.add_argument("--new-run-id", required=True); recheck.add_argument("--baseline-pin")
    recheck.set_defaults(func=cmd_recheck)
    status = sub.add_parser("status", help="show persisted run status")
    status.add_argument("--root", required=True); status.add_argument("--run-id", required=True); status.set_defaults(func=cmd_status)
    resume = sub.add_parser("resume", help="summarize state and next action")
    resume.add_argument("--root", required=True); resume.add_argument("--run-id", required=True); resume.set_defaults(func=cmd_resume)
    repair = sub.add_parser("repair", help="record a bounded repair round")
    repair.add_argument("--root", required=True); repair.add_argument("--run-id", required=True); repair.add_argument("--reason", required=True); repair.set_defaults(func=cmd_repair)
    close = sub.add_parser("close", help="record a terminal non-pass state")
    close.add_argument("--root", required=True); close.add_argument("--run-id", required=True); close.add_argument("--state", choices=["failed", "blocked", "cancelled", "superseded"], required=True); close.add_argument("--reason", required=True); close.set_defaults(func=cmd_close)
    observe = sub.add_parser("observe", help="record observed invocation/spend metrics without claiming enforcement")
    observe.add_argument("--root", required=True); observe.add_argument("--run-id", required=True); observe.add_argument("--invocations", type=int, required=True); observe.add_argument("--spend", help="observed amount/currency or 'not available'"); observe.add_argument("--source", required=True, help="evidence source for the observation"); observe.set_defaults(func=cmd_observe)
    validate = sub.add_parser("validate", help="validate receipt shape and input freshness")
    validate.add_argument("--root", required=True); validate.add_argument("--run-id", required=True); validate.add_argument("--baseline-pin", help="same externally governed acceptance pin used at start"); validate.set_defaults(func=cmd_validate)
    drift = sub.add_parser("drift", help="read-only source-versus-generated-view JSON check; writes a new evidence log")
    drift.add_argument("--root", required=True); drift.add_argument("--run-id", required=True); drift.add_argument("--source", required=True); drift.add_argument("--view", required=True); drift.add_argument("--log", required=True); drift.set_defaults(func=cmd_drift)
    orchestration = sub.add_parser("orchestrate", help="run or resume a bounded synthetic projection plan; advisory only")
    orchestration.add_argument("--root", required=True)
    orchestration.add_argument("--run-id", required=True)
    orchestration.add_argument("--plan", required=True)
    orchestration.add_argument("--max-steps", type=int, default=3)
    def orchestrate(args):
        from orchestration import command
        return command(args)
    orchestration.set_defaults(func=orchestrate)
    agent_run = sub.add_parser("agent-run", help="run/resume bounded native Codex agents with quality and verifier gates")
    agent_run.add_argument("--root", required=True); agent_run.add_argument("--run-id", required=True)
    agent_run.add_argument("--plan", required=True); agent_run.add_argument("--max-steps", type=int, default=100)
    def run_agents(args):
        from agent_bridge import command
        return command(args)
    agent_run.set_defaults(func=run_agents)
    agent_cancel = sub.add_parser("agent-cancel", help="request cancellation without redispatching work")
    agent_cancel.add_argument("--root", required=True); agent_cancel.add_argument("--run-id", required=True)
    def cancel_agents(args):
        from agent_bridge import cancel_command
        return cancel_command(args)
    agent_cancel.set_defaults(func=cancel_agents)
    for name, help_text in [('agent-preflight', 'inspect plan/runtime dependencies without dispatch'),
                            ('agent-status', 'inspect progress, evidence and recovery without writes'),
                            ('agent-recover', 'finish a journaled verified promotion; never redispatch agents')]:
        command = sub.add_parser(name, help=help_text)
        command.add_argument('--root', required=True)
        command.add_argument('--plan', required=True)
        if name != 'agent-preflight':
            command.add_argument('--run-id', required=True)
        if name == 'agent-recover':
            command.add_argument('--finish-promotion', action='store_true', required=True)
        def bridge_command(args, name=name):
            from agent_bridge import preflight_command, status_command, recover_command
            return {'agent-preflight': preflight_command, 'agent-status': status_command,
                    'agent-recover': recover_command}[name](args)
        command.set_defaults(func=bridge_command)
    profile_init = sub.add_parser('profile-init', help='configure reusable project scope and checks once')
    profile_init.add_argument('--root', required=True)
    profile_init.add_argument('--profile', default='proofloop-project.json')
    profile_init.add_argument('--from-plan')
    profile_init.add_argument('--editable', nargs='+')
    profile_init.add_argument('--protected', nargs='+')
    profile_init.add_argument('--context', nargs='+')
    profile_init.add_argument('--check-json', action='append', help='trusted command as a JSON argv array')
    def initialize_profile(args):
        from task_workflow import initialize_profile
        return initialize_profile(args)
    profile_init.set_defaults(func=initialize_profile)
    fix = sub.add_parser('fix', help='describe a fix once; return a verified diff or precise blocker')
    fix.add_argument('--root', required=True)
    fix.add_argument('--profile', default='proofloop-project.json')
    fix.add_argument('--run-id', help='reuse the exact request/profile to resume; defaults to a unique ID')
    request = fix.add_mutually_exclusive_group(required=True)
    request.add_argument('--request')
    request.add_argument('--request-file', help='project-relative UTF-8 request file')
    fix.add_argument('--json', action='store_true', help='emit the structured outcome/evidence report')
    def fix_task(args):
        from task_workflow import command
        return command(args)
    fix.set_defaults(func=fix_task)
    for name, only in [('harness-run', False), ('harness-check', True)]:
        task = sub.add_parser(name, help='run a typed task contract' if not only else 'run checks without dispatching agents')
        task.add_argument('--root', required=True); task.add_argument('--run-id', required=True)
        task.add_argument('--task', required=True)
        task.add_argument('--max-steps', type=int, default=100)
        def run_harness(args):
            from harness import command
            return command(args)
        task.set_defaults(func=run_harness, checks_only=only)
    observer = sub.add_parser('harness-status', help='inspect typed task evidence without execution')
    observer.add_argument('--root', required=True); observer.add_argument('--run-id', required=True)
    observer.add_argument('--task', required=True)
    def inspect_harness(args):
        from harness import status_command
        return status_command(args)
    observer.set_defaults(func=inspect_harness)
    stop = sub.add_parser('harness-cancel', help='request cancellation of a typed task run')
    stop.add_argument('--root', required=True); stop.add_argument('--run-id', required=True)
    stop.set_defaults(func=cancel_agents)
    recovery = sub.add_parser('harness-recover', help='finish a journaled typed-task promotion without redispatch')
    recovery.add_argument('--root', required=True); recovery.add_argument('--run-id', required=True)
    recovery.add_argument('--task', required=True); recovery.add_argument('--finish-promotion', action='store_true', required=True)
    def recover_harness(args):
        from harness import recover_command
        return recover_command(args)
    recovery.set_defaults(func=recover_harness)
    adapters = sub.add_parser('harness-adapters', help='list version-1 built-in check adapters')
    def show_adapters(args):
        from harness_checks import ADAPTERS
        print(canonical_json({'schema_version': 1, 'checks': ADAPTERS, 'hosts': ['codex', 'command']}), end='')
        return 0
    adapters.set_defaults(func=show_adapters)
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        if hasattr(args, "run_id") and args.run_id is not None and (not isinstance(args.run_id, str) or not RUN_ID_RE.fullmatch(args.run_id)):
            raise ValueError("run id must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}")
        return args.func(args)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
