import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "proofloop"


def run_cli(*args, expected=0):
    args = list(map(str, args))
    command = args[0] if args else ""
    if command in {"start", "validate"} and "--baseline-pin" not in args and "--advisory" not in args:
        root = Path(args[args.index("--root") + 1]).resolve()
        pin_path = root.parent / f"{root.name}-trusted-acceptance-pin.json"
        if not pin_path.exists():
            acceptance_paths = [args[i + 1] for i, value in enumerate(args[:-1]) if value == "--acceptance-test"] if command == "start" else []
            tests = {}
            for relative in acceptance_paths:
                data = (root / relative).read_bytes()
                tests[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            if tests:
                pin_path.write_text(json.dumps({"schema_version": 1, "acceptance_tests": tests}, sort_keys=True, indent=2) + "\n")
        args.extend(["--baseline-pin", str(pin_path)])
    result = subprocess.run([sys.executable, str(CLI), *args], text=True, capture_output=True)
    if result.returncode != expected:
        raise AssertionError(f"expected exit {expected}, got {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}")
    if command == "start" and result.returncode == 0 and "--advisory" not in args:
        # Test-fixture approver: model an independent custodian approving this
        # synthetic Run Card after start. Production never self-approves here.
        root = Path(args[args.index("--root") + 1]).resolve()
        pin_path = Path(args[args.index("--baseline-pin") + 1]).resolve()
        run_id = args[args.index("--run-id") + 1]
        card_path = root / ".proof" / "runs" / run_id / "run-card.json"
        pin = json.loads(pin_path.read_text())
        pin["approved_run_card_sha256"] = hashlib.sha256(card_path.read_bytes()).hexdigest()
        pin_path.write_text(json.dumps(pin, sort_keys=True, indent=2) + "\n")
    return result


class ProofLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="proofloop-synthetic-")
        self.root = Path(self.temp.name)
        self.drift_serial = 0
        run_cli("init", self.root)
        self.fixtures = self.root / "fixtures"
        self.fixtures.mkdir()
        (self.fixtures / "input.json").write_text('{"sample":"synthetic"}\n', encoding="utf-8")
        (self.fixtures / "acceptance.json").write_text('{"check":"fixed synthetic contract"}\n', encoding="utf-8")
        (self.fixtures / "records.json").write_text('{"items":[{"id":"SYN-1"},{"id":"SYN-2"}],"fixture_classification":"synthetic"}\n', encoding="utf-8")
        (self.fixtures / "view.json").write_text('{"record_count":2,"ids":["SYN-1","SYN-2"],"fixture_classification":"synthetic"}\n', encoding="utf-8")
        run_cli("start", "--root", self.root, "--goal", "validate synthetic fixture", "--scope", "fixtures only", "--run-id", "demo", "--input", "fixtures/input.json", "--input", "fixtures/records.json", "--input", "fixtures/view.json", "--acceptance-test", "fixtures/acceptance.json", "--check", "source-view-drift", "--done", "record count and ordered IDs match")

    def tearDown(self):
        self.temp.cleanup()

    def run_drift(self, log=".proof/runs/demo/logs/drift.json", expected=0):
        return run_cli("drift", "--root", self.root, "--run-id", "demo", "--source", "fixtures/records.json", "--view", "fixtures/view.json", "--log", log, expected=expected)

    def make_passing_receipt(self):
        self.drift_serial += 1
        relative_log = f".proof/runs/demo/logs/drift-{self.drift_serial}.json"
        result = json.loads(self.run_drift(log=relative_log).stdout)
        log_path = self.root / relative_log
        output = self.root / "output.txt"
        output.write_text("Synthetic result.\n", encoding="utf-8")
        receipt_path = self.root / ".proof/runs/demo/receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        manifest = self.root / ".proof/runs/demo/input-manifest.json"
        baseline_path = self.root / ".proof/baselines/demo.json"
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        receipt.update({
            "run_state": "passed_for_scope",
            "input_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "acceptance_tests_sha256": hashlib.sha256(json.dumps(baseline["acceptance_tests"], sort_keys=True, ensure_ascii=False, indent=2).encode() + b"\n").hexdigest(),
            "acceptance_baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
            "checks": {"source-view-drift": {"state": "pass", "reason": "built-in drift runner recomputed projection", "log": {"path": relative_log, "sha256": hashlib.sha256(log_path.read_bytes()).hexdigest(), "runner": "proofloop-drift"}}},
            "independent_review": {"status": "not_applicable", "reviewer": "", "fresh_context": False, "notes": "", "reason": "Synthetic low-risk local fixture."},
            "claims": [{"label": "Verified", "claim_type": "check_pass", "check": "source-view-drift", "supports_checks": ["source-view-drift"], "claim": "Check source-view-drift passed.", "evidence": [relative_log]}],
            "coverage": {"checked": result["coverage"], "not_checked": ["external integrations", "security isolation"]},
            "exceptions": [],
            "outputs": {"output.txt": hashlib.sha256(output.read_bytes()).hexdigest()},
            "human_decision": "not_required",
        })
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    def make_failed_drift_receipt(self, claimed_state="pass"):
        # Independent counterexample: divergent view is declared BEFORE intake,
        # so every input hash is fresh. Keep the genuine failing runner log.
        view = self.fixtures / "divergent.json"
        view.write_text('{"record_count":99,"ids":[],"fixture_classification":"synthetic"}\n')
        run_cli("start", "--root", self.root, "--goal", "failed evidence counterexample", "--scope", "synthetic only", "--risk", "low", "--run-id", "negative", "--input", "fixtures/records.json", "--input", "fixtures/divergent.json", "--acceptance-test", "fixtures/acceptance.json", "--check", "source-view-drift", "--done", "count and ordered IDs match")
        relative_log = ".proof/runs/negative/logs/drift.json"
        result = run_cli("drift", "--root", self.root, "--run-id", "negative", "--source", "fixtures/records.json", "--view", "fixtures/divergent.json", "--log", relative_log, expected=1)
        log = json.loads(result.stdout)
        self.assertEqual(log["state"], "fail")
        self.assertEqual(log["exit_code"], result.returncode)
        path = self.root / ".proof/runs/negative/receipt.json"
        receipt = json.loads(path.read_text())
        receipt.update({
            "run_state": "passed_for_scope" if claimed_state == "pass" else "failed",
            "checks": {"source-view-drift": {"state": claimed_state, "reason": "Synthetic divergence", "log": {"path": relative_log, "sha256": hashlib.sha256((self.root / relative_log).read_bytes()).hexdigest(), "runner": "proofloop-drift"}}},
            "independent_review": {"status": "not_applicable", "reason": "Synthetic low-risk consistency fixture."},
            "claims": [{"label": "Verified", "claim_type": "check_pass", "check": "source-view-drift", "supports_checks": ["source-view-drift"], "claim": "Check source-view-drift passed.", "evidence": [relative_log]}] if claimed_state == "pass" else [{"label": "Reported", "claim": "The drift runner reported failure.", "source": relative_log}],
            "coverage": {"checked": log["coverage"] if claimed_state == "pass" else [], "not_checked": ["external integrations"]},
            "outputs": {"fixtures/divergent.json": hashlib.sha256(view.read_bytes()).hexdigest()},
            "human_decision": "not_required",
        })
        path.write_text(json.dumps(receipt) + "\n")
        return path, self.root / relative_log

    def test_genuine_failed_runner_cannot_be_claimed_pass(self):
        _, log = self.make_failed_drift_receipt()
        before = log.read_bytes()
        result = run_cli("validate", "--root", self.root, "--run-id", "negative", expected=2)
        report = json.loads(result.stdout)
        self.assertFalse(report["valid"])
        self.assertTrue(report["input_fresh"])
        self.assertIn("recomputed fail", result.stdout)
        self.assertEqual(log.read_bytes(), before)

    def test_genuine_failed_receipt_remains_failed_and_unaccepted(self):
        path, log = self.make_failed_drift_receipt(claimed_state="fail")
        before = log.read_bytes()
        run_cli("close", "--root", self.root, "--run-id", "negative", "--state", "failed", "--reason", "Genuine synthetic divergence")
        result = run_cli("validate", "--root", self.root, "--run-id", "negative", expected=2)
        self.assertIn("check source-view-drift is fail", result.stdout)
        status = json.loads(run_cli("status", "--root", self.root, "--run-id", "negative").stdout)
        self.assertEqual(status["run_state"], "failed")
        self.assertEqual(json.loads(path.read_text())["checks"]["source-view-drift"]["state"], "fail")
        self.assertEqual(log.read_bytes(), before)

    def test_pass_runner_requires_matching_integer_exit_and_version(self):
        for field, value in [("exit_code", 1), ("exit_code", -1), ("exit_code", "0"), ("exit_code", False), ("exit_code", None), ("runner_version", "0.2.0"), ("schema_version", 1)]:
            with self.subTest(field=field, value=value):
                self.make_passing_receipt()
                path = self.root / ".proof/runs/demo/receipt.json"
                receipt = json.loads(path.read_text())
                evidence = self.root / receipt["checks"]["source-view-drift"]["log"]["path"]
                log = json.loads(evidence.read_text())
                self.assertEqual(log["state"], "pass")
                self.assertEqual(log["exit_code"], 0)
                if value is None:
                    del log[field]
                else:
                    log[field] = value
                evidence.write_text(json.dumps(log) + "\n")
                receipt["checks"]["source-view-drift"]["log"]["sha256"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
                path.write_text(json.dumps(receipt) + "\n")
                rejected = run_cli("validate", "--root", self.root, "--run-id", "demo", expected=2)
                self.assertIn("evidence is unverified", rejected.stdout)

    def test_failed_runner_state_or_exit_forgery_cannot_pass(self):
        path, evidence = self.make_failed_drift_receipt()
        genuine = json.loads(evidence.read_text())
        for state, exit_code in [("fail", 0), ("pass", 0), ("pass", 1)]:
            with self.subTest(state=state, exit_code=exit_code):
                log = dict(genuine, state=state, exit_code=exit_code)
                if state == "pass":
                    log["reason"] = "generated view matches the declared source projection"
                evidence.write_text(json.dumps(log) + "\n")
                receipt = json.loads(path.read_text())
                receipt["checks"]["source-view-drift"]["log"]["sha256"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
                path.write_text(json.dumps(receipt) + "\n")
                rejected = run_cli("validate", "--root", self.root, "--run-id", "negative", expected=2)
                self.assertIn("does not match recomputed", rejected.stdout)

    def test_receipt_pass_then_stale(self):
        self.make_passing_receipt()
        passed = run_cli("validate", "--root", self.root, "--run-id", "demo")
        self.assertTrue(json.loads(passed.stdout)["valid"])
        (self.fixtures / "input.json").write_text('{"sample":"changed"}\n', encoding="utf-8")
        stale = run_cli("validate", "--root", self.root, "--run-id", "demo", expected=2)
        self.assertIn("stale", stale.stdout)
        (self.root / "output.txt").write_text("Changed after review.\n", encoding="utf-8")
        output_stale = run_cli("validate", "--root", self.root, "--run-id", "demo", expected=2)
        self.assertIn("output hash mismatch", output_stale.stdout)

    def test_acceptance_baseline_detects_mutated_test_and_rewritten_manifest_receipt(self):
        self.make_passing_receipt()
        (self.fixtures / "acceptance.json").write_text('{"check":"rewritten"}\n', encoding="utf-8")
        manifest_path = self.root / ".proof/runs/demo/input-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["acceptance_tests"]["fixtures/acceptance.json"] = {"sha256": hashlib.sha256((self.fixtures / "acceptance.json").read_bytes()).hexdigest(), "bytes": (self.fixtures / "acceptance.json").stat().st_size}
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
        receipt_path = self.root / ".proof/runs/demo/receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["input_manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        result = run_cli("validate", "--root", self.root, "--run-id", "demo", expected=2)
        self.assertIn("baseline", result.stdout)
        self.assertIn("stale", result.stdout)

    def test_baseline_history_tampering_is_detected(self):
        self.make_passing_receipt()
        path = self.root / ".proof/baselines/demo.json"
        path.chmod(0o600)
        baseline = json.loads(path.read_text())
        baseline["acceptance_tests"]["fixtures/acceptance.json"]["sha256"] = "0" * 64
        path.write_text(json.dumps(baseline, indent=2) + "\n")
        result = run_cli("validate", "--root", self.root, "--run-id", "demo", expected=2)
        self.assertIn("baseline", result.stdout)

    def test_repairs_stop_after_two_rounds(self):
        run_cli("repair", "--root", self.root, "--run-id", "demo", "--reason", "first bounded repair")
        run_cli("repair", "--root", self.root, "--run-id", "demo", "--reason", "second bounded repair")
        third = run_cli("repair", "--root", self.root, "--run-id", "demo", "--reason", "must escalate", expected=3)
        self.assertIn("repair limit reached", third.stderr)
        status = json.loads(run_cli("resume", "--root", self.root, "--run-id", "demo").stdout)
        self.assertEqual(status["run_state"], "blocked")

    def test_high_risk_requires_human_decision(self):
        run_cli("start", "--root", self.root, "--goal", "synthetic high risk", "--scope", "fixtures", "--risk", "high", "--run-id", "high", "--acceptance-test", "fixtures/acceptance.json", "--check", "source-view-drift", "--done", "decision captured")
        rpath = self.root / ".proof/runs/high/receipt.json"
        receipt = json.loads(rpath.read_text(encoding="utf-8"))
        receipt["run_state"] = "passed_for_scope"
        rpath.write_text(json.dumps(receipt), encoding="utf-8")
        check = run_cli("validate", "--root", self.root, "--run-id", "high", expected=2)
        self.assertIn("human decision is required", check.stdout)

    def test_invalid_acceptance_path_does_not_strand_run_id(self):
        result = run_cli("start", "--root", self.root, "--goal", "invalid input", "--scope", "fixture", "--run-id", "retryable", "--acceptance-test", "fixtures/missing.json", "--check", "source-view-drift", "--done", "done", expected=2)
        self.assertIn("does not exist", result.stderr)
        self.assertFalse((self.root / ".proof/runs/retryable").exists())
        self.assertFalse((self.root / ".proof/baselines/retryable.json").exists())
        run_cli("start", "--root", self.root, "--goal", "valid retry", "--scope", "fixture", "--run-id", "retryable", "--acceptance-test", "fixtures/acceptance.json", "--check", "source-view-drift", "--done", "done")

    def test_run_id_traversal_rejected_by_every_run_command(self):
        victim = self.root.parent / f"victim-{os.getpid()}.json"
        victim.write_text('{"run_state":"in_progress"}\n')
        before = hashlib.sha256(victim.read_bytes()).hexdigest()
        commands = [
            ("status",), ("resume",), ("repair", "--reason", "bad id"),
            ("close", "--state", "blocked", "--reason", "bad id"),
            ("observe", "--invocations", "0", "--spend", "0", "--source", "synthetic"),
            ("validate",), ("drift", "--source", "fixtures/records.json", "--view", "fixtures/view.json", "--log", ".proof/evil.json"),
        ]
        for extra in commands:
            args = [*extra]
            if extra[0] == "drift":
                args.extend(["--run-id", "../../" + victim.stem])
            else:
                args.extend(["--run-id", "../../" + victim.stem])
            result = run_cli(args[0], "--root", self.root, *args[1:], expected=2)
            self.assertIn("run id", result.stderr)
        self.assertEqual(hashlib.sha256(victim.read_bytes()).hexdigest(), before)
        victim.unlink()

    def test_symlink_input_and_run_receipt_are_rejected(self):
        outside = self.root.parent / f"outside-{os.getpid()}.json"
        outside.write_text('{"sensitive":"synthetic only"}')
        (self.fixtures / "linked.json").symlink_to(outside)
        bad = run_cli("start", "--root", self.root, "--goal", "symlink", "--scope", "fixture", "--run-id", "sym", "--input", "fixtures/linked.json", "--acceptance-test", "fixtures/acceptance.json", "--check", "source-view-drift", "--done", "done", expected=2)
        self.assertIn("symlink", bad.stderr)
        receipt = self.root / ".proof/runs/demo/receipt.json"
        original = receipt.read_bytes()
        receipt.unlink()
        receipt.symlink_to(outside)
        result = run_cli("status", "--root", self.root, "--run-id", "demo", expected=2)
        self.assertIn("symlink", result.stderr)
        receipt.unlink()
        receipt.write_bytes(original)
        outside.unlink()

    def test_policy_deny_and_review_capabilities_cannot_be_selected(self):
        path = self.root / ".proof/capabilities.json"
        registry = json.loads(path.read_text())
        registry["capabilities"].append({"id": "restricted-tool", "policy": "deny", "lifecycle": "tested", "version_or_pin": "synthetic-fixture-1", "evidence": "synthetic deny fixture", "health_check": "synthetic", "license_status": "Reported", "telemetry_status": "Reported", "use_when": [], "not_for": ["all tasks"]})
        path.write_text(json.dumps(registry))
        rejected = run_cli("start", "--root", self.root, "--goal", "denied", "--scope", "fixture", "--run-id", "denied", "--use-capability", "restricted-tool", "--acceptance-test", "fixtures/acceptance.json", "--check", "source-view-drift", "--done", "done", expected=2)
        self.assertIn("not selectable", rejected.stderr)
        self.assertFalse((self.root / ".proof/runs/denied").exists())
        registry["capabilities"][-1]["policy"] = "review"
        path.write_text(json.dumps(registry))
        rejected = run_cli("start", "--root", self.root, "--goal", "review", "--scope", "fixture", "--run-id", "review", "--use-capability", "restricted-tool", "--acceptance-test", "fixtures/acceptance.json", "--check", "source-view-drift", "--done", "done", expected=2)
        self.assertIn("not selectable", rejected.stderr)

    def test_fabricated_runner_and_unsupported_verified_claim_are_rejected(self):
        self.make_passing_receipt()
        log_path = self.root / ".proof/runs/demo/logs/drift-1.json"
        log = json.loads(log_path.read_text())
        log["runner"] = "made-up-runner"
        log_path.write_text(json.dumps(log) + "\n")
        receipt_path = self.root / ".proof/runs/demo/receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["checks"]["source-view-drift"]["log"]["runner"] = "made-up-runner"
        receipt["checks"]["source-view-drift"]["log"]["sha256"] = hashlib.sha256(log_path.read_bytes()).hexdigest()
        receipt_path.write_text(json.dumps(receipt) + "\n")
        result = run_cli("validate", "--root", self.root, "--run-id", "demo", expected=2)
        self.assertIn("unrecognized runner/check", result.stdout)
        self.make_passing_receipt()
        receipt = json.loads(receipt_path.read_text())
        receipt["claims"][0]["claim"] = "Production browser security and external upload checks passed."
        receipt_path.write_text(json.dumps(receipt) + "\n")
        result = run_cli("validate", "--root", self.root, "--run-id", "demo", expected=2)
        self.assertIn("exact structured check-pass", result.stdout)

    def test_drift_source_and_view_must_be_declared_inputs(self):
        (self.fixtures / "unlisted-source.json").write_text('{"items":[{"id":"SYN-X"}]}\n')
        (self.fixtures / "unlisted-view.json").write_text('{"record_count":1,"ids":["SYN-X"],"fixture_classification":"synthetic"}\n')
        result = run_cli("drift", "--root", self.root, "--run-id", "demo", "--source", "fixtures/unlisted-source.json", "--view", "fixtures/unlisted-view.json", "--log", ".proof/runs/demo/logs/unlisted.json", expected=2)
        self.assertIn("declared Run Card inputs", result.stderr)
        self.assertFalse((self.root / ".proof/runs/demo/logs/unlisted.json").exists())

    def test_capability_selection_records_only_policy_and_lifecycle_filtering(self):
        path = self.root / ".proof/capabilities.json"
        registry = json.loads(path.read_text())
        registry["capabilities"][0]["use_when"] = []
        registry["capabilities"][0]["not_for"] = ["all tasks"]
        registry["capabilities"][0]["health_check"] = "operator supplied text; not executed"
        path.write_text(json.dumps(registry))
        run_cli("start", "--root", self.root, "--goal", "synthetic capability selection", "--scope", "fixture", "--run-id", "selection-note", "--use-capability", "python-stdlib", "--acceptance-test", "fixtures/acceptance.json", "--check", "source-view-drift", "--done", "selection is transparently recorded")
        card = json.loads((self.root / ".proof/runs/selection-note/run-card.json").read_text())
        self.assertEqual(card["selection_order_applied"], ["policy", "lifecycle"])
        self.assertIn("not_for", card["selection_metadata_not_evaluated"])
        self.assertIn("not evaluated or executed", card["capability_selection_note"])

    def test_advisory_run_is_usable_but_cannot_be_accepted(self):
        run_cli("start", "--root", self.root, "--goal", "advisory synthetic check", "--scope", "fixture only", "--run-id", "advisory", "--advisory", "--acceptance-test", "fixtures/acceptance.json", "--check", "source-view-drift", "--done", "record evidence")
        card = json.loads((self.root / ".proof/runs/advisory/run-card.json").read_text())
        self.assertEqual(card["acceptance_mode"], "advisory")
        result = run_cli("validate", "--root", self.root, "--run-id", "advisory", expected=2)
        self.assertIn("advisory", result.stdout)
        receipt_path = self.root / ".proof/runs/advisory/receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["run_state"] = "passed_for_scope"
        receipt_path.write_text(json.dumps(receipt) + "\n")
        forged = run_cli("validate", "--root", self.root, "--run-id", "advisory", expected=2)
        self.assertIn("cannot be accepted", forged.stdout)

    def test_acceptance_required_run_needs_external_pin(self):
        result = subprocess.run([sys.executable, str(CLI), "start", "--root", str(self.root), "--goal", "missing pin", "--scope", "fixture", "--run-id", "missing-pin", "--acceptance-test", "fixtures/acceptance.json", "--check", "source-view-drift", "--done", "must block"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("acceptance-required runs need an externally governed --baseline-pin", result.stderr)
        self.assertFalse((self.root / ".proof/runs/missing-pin").exists())

    def test_run_card_tampering_after_external_approval_is_rejected(self):
        self.make_passing_receipt()
        card_path = self.root / ".proof/runs/demo/run-card.json"
        card = json.loads(card_path.read_text())
        card["risk"] = "low"
        card["human_decision_required"] = False
        card["required_checks"] = []
        card["verification_mode"] = "fast_checks_with_independent_review_if_scope_requires"
        card_path.write_text(json.dumps(card, sort_keys=True, indent=2) + "\n")
        result = run_cli("validate", "--root", self.root, "--run-id", "demo", expected=2)
        self.assertIn("Run Card changed after external reviewer approval", result.stdout)

    def test_external_pin_requires_reviewer_card_approval(self):
        self.make_passing_receipt()
        pin_path = self.root.parent / f"{self.root.name}-trusted-acceptance-pin.json"
        pin = json.loads(pin_path.read_text())
        pin.pop("approved_run_card_sha256", None)
        pin_path.write_text(json.dumps(pin, sort_keys=True, indent=2) + "\n")
        result = run_cli("validate", "--root", self.root, "--run-id", "demo", expected=2)
        self.assertIn("lacks reviewer approval of the Run Card hash", result.stdout)

    def test_in_project_pin_is_rejected(self):
        pin_path = self.root / "fixtures/local-pin.json"
        accepted = self.root / "fixtures/acceptance.json"
        pin_path.write_text(json.dumps({"schema_version": 1, "acceptance_tests": {"fixtures/acceptance.json": {"sha256": hashlib.sha256(accepted.read_bytes()).hexdigest(), "bytes": accepted.stat().st_size}}}))
        result = subprocess.run([sys.executable, str(CLI), "start", "--root", str(self.root), "--goal", "in-project pin", "--scope", "fixture", "--run-id", "local-pin", "--acceptance-test", "fixtures/acceptance.json", "--baseline-pin", str(pin_path), "--check", "source-view-drift", "--done", "must block"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("must be outside the project", result.stderr)
        self.assertFalse((self.root / ".proof/runs/local-pin").exists())

    def test_empty_checker_metadata_is_rejected(self):
        self.make_passing_receipt()
        path = self.root / ".proof/runs/demo/receipt.json"
        receipt = json.loads(path.read_text())
        receipt["checker_version"] = ""
        receipt["environment"] = {}
        path.write_text(json.dumps(receipt) + "\n")
        result = run_cli("validate", "--root", self.root, "--run-id", "demo", expected=2)
        self.assertIn("checker_version", result.stdout)
        self.assertIn("environment", result.stdout)

    def test_wrong_checker_version_is_rejected(self):
        self.make_passing_receipt()
        path = self.root / ".proof/runs/demo/receipt.json"
        receipt = json.loads(path.read_text())
        receipt["checker_version"] = "0.1.0"
        path.write_text(json.dumps(receipt) + "\n")
        result = run_cli("validate", "--root", self.root, "--run-id", "demo", expected=2)
        self.assertIn("executing CLI version", result.stdout)

    def test_interrupted_run_resumes_from_persisted_state(self):
        state = json.loads(run_cli("resume", "--root", self.root, "--run-id", "demo").stdout)
        self.assertEqual(state["last_event"]["event"], "started")
        self.assertIn("complete the receipt", state["next_action"])

    def test_missing_required_check_and_expiry_block(self):
        self.make_passing_receipt()
        path = self.root / ".proof/runs/demo/receipt.json"
        receipt = json.loads(path.read_text())
        receipt["checks"] = {}
        receipt["expires_at"] = "2000-01-01T00:00:00Z"
        path.write_text(json.dumps(receipt))
        rejected = run_cli("validate", "--root", self.root, "--run-id", "demo", expected=2)
        self.assertIn("required check is missing", rejected.stdout)
        self.assertIn("expired", rejected.stdout)

    def test_version_precondition_rejects_stale_write(self):
        sys.path.insert(0, str(ROOT))
        import proofloop
        path = self.root / "cas.json"
        path.write_text('{"value":1}\n')
        old_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        path.write_text('{"value":2}\n')
        with self.assertRaisesRegex(ValueError, "stale write rejected"):
            proofloop.write_json(path, {"value": 3}, expected_sha256=old_hash)
        self.assertEqual(json.loads(path.read_text())["value"], 2)

    def test_two_concurrent_repairs_cannot_overwrite_each_other(self):
        lock = self.root / ".proof.write.lock"
        lock.write_text("test-held-lock")
        cmd = [sys.executable, str(CLI), "repair", "--root", str(self.root), "--run-id", "demo", "--reason", "concurrent synthetic writer"]
        first = subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        second = subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(0.3)
        lock.unlink()
        outcomes = [first.communicate(timeout=10), second.communicate(timeout=10)]
        codes = [first.returncode, second.returncode]
        self.assertEqual(codes.count(0), 1, outcomes)
        self.assertEqual(codes.count(2), 1, outcomes)
        self.assertEqual(json.loads((self.root / ".proof/runs/demo/receipt.json").read_text())["revision"], 1)

    def test_drift_check_recomputes_and_does_not_overwrite(self):
        passed = self.run_drift()
        self.assertEqual(json.loads(passed.stdout)["state"], "pass")
        overwrite = self.run_drift(expected=2)
        self.assertIn("refusing to overwrite", overwrite.stderr)
        log = json.loads((self.root / ".proof/runs/demo/logs/drift.json").read_text())
        self.assertEqual(log["runner"], "proofloop-drift")

    def test_divergent_view_is_reported_without_source_writes(self):
        source = self.fixtures / "records.json"
        view = self.fixtures / "view.json"
        view.write_text('{"record_count":99,"ids":[],"fixture_classification":"synthetic"}\n', encoding="utf-8")
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        result = self.run_drift(log=".proof/runs/demo/logs/divergence.json", expected=1)
        self.assertEqual(json.loads(result.stdout)["state"], "fail")
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
