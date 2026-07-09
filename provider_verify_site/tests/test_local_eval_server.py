import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from provider_verify_site.scripts.local_eval_server import (
    DEFAULT_ALLOWED_ORIGINS,
    LocalEvalError,
    build_lite_gate_record,
    create_quick_screen_run,
    create_lite_gate_run,
    list_provider_models_for_payload,
    read_quick_screen_run,
    safe_slug,
    validate_provider_models_payload,
    validate_quick_screen_payload,
    validate_lite_gate_payload,
)
from provider_verify_site.scripts.provider_runner import ProviderRequestError


class LocalEvalServerTests(unittest.TestCase):
    def sample_payload(self):
        return {
            "candidate_alias": "opus 4.8 syapi",
            "claimed_model": "Claude Opus 4.8",
            "prompt_pack": "pack_a_default",
            "memory_status": "off",
            "raw_candidate_output": "# Lite Gate Candidate Output\nraw answer",
            "scores": {
                "task_1_project_handoff": 16,
                "task_2_boundary_safety": 17,
                "task_3_evidence_execution": 15,
                "task_4_coding": 18,
                "task_5_chinese_product_communication": 16,
                "total": 82,
            },
            "code_quality": {
                "correctness": 5,
                "change_precision": 5,
                "testing_awareness": 4,
                "maintainability": 5,
                "total": 19,
                "status": "pass",
            },
            "label": "PASS",
            "can_use_for_coding": True,
            "next_action": "coding_trial",
            "reviewer_notes": "Good coding signal.",
        }

    def sample_quick_screen_payload(self):
        return {
            "provider_alias": "opus_4_8syapi",
            "claimed_model": "Claude Opus 4.8",
            "base_url": "https://provider.example/v1",
            "api_key": "session-only-secret",
            "model_name": "claude-opus-4-8",
            "provider_protocol": "auto",
            "eval_mode": "quick_screen_v1",
        }

    def sample_coding_probe_payload(self):
        payload = self.sample_quick_screen_payload()
        payload["provider_alias"] = "opus_4_8syapi_code_probe"
        payload["eval_mode"] = "coding_probe_v1"
        return payload

    def sample_screen_v2_payload(self):
        payload = self.sample_quick_screen_payload()
        payload["provider_alias"] = "opus_4_8syapi_screen_v2"
        payload["eval_mode"] = "screen_v2"
        return payload

    def sample_holdout_screen_payload(self):
        payload = self.sample_quick_screen_payload()
        payload["provider_alias"] = "opus_4_8syapi_holdout"
        payload["eval_mode"] = "holdout_screen_v1"
        return payload

    def sample_agent_tool_use_payload(self):
        payload = self.sample_quick_screen_payload()
        payload["provider_alias"] = "agent_tool_route"
        payload["eval_mode"] = "agent_tool_use_v1"
        return payload

    def sample_factuality_payload(self):
        payload = self.sample_quick_screen_payload()
        payload["provider_alias"] = "factuality_route"
        payload["eval_mode"] = "factuality_calibration_v1"
        return payload

    def test_safe_slug_keeps_ascii_and_collapses_separators(self):
        self.assertEqual(safe_slug("Opus 4.8 SYAPI"), "opus_4_8_syapi")
        self.assertEqual(safe_slug("provider/v2 (test)"), "provider_v2_test")
        self.assertEqual(safe_slug("一日"), "candidate")

    def test_validate_lite_gate_payload_accepts_complete_payload(self):
        payload = self.sample_payload()

        normalized = validate_lite_gate_payload(payload)

        self.assertEqual(normalized["candidate_alias"], "opus 4.8 syapi")
        self.assertEqual(normalized["prompt_pack"], "pack_a_default")
        self.assertIs(normalized["can_use_for_coding"], True)
        self.assertEqual(normalized["scores"]["total"], 82)

    def test_validate_lite_gate_payload_rejects_invalid_scores(self):
        payload = self.sample_payload()
        payload["scores"]["total"] = 101

        with self.assertRaises(LocalEvalError):
            validate_lite_gate_payload(payload)

    def test_validate_quick_screen_payload_requires_session_only_fields(self):
        payload = self.sample_quick_screen_payload()
        del payload["api_key"]

        with self.assertRaises(LocalEvalError):
            validate_quick_screen_payload(payload)

    def test_validate_quick_screen_payload_accepts_coding_probe_mode(self):
        normalized = validate_quick_screen_payload(self.sample_coding_probe_payload())

        self.assertEqual(normalized["eval_mode"], "coding_probe_v1")

    def test_validate_quick_screen_payload_accepts_screen_v2_mode(self):
        normalized = validate_quick_screen_payload(self.sample_screen_v2_payload())

        self.assertEqual(normalized["eval_mode"], "screen_v2")

    def test_validate_quick_screen_payload_accepts_holdout_screen_mode(self):
        normalized = validate_quick_screen_payload(self.sample_holdout_screen_payload())

        self.assertEqual(normalized["eval_mode"], "holdout_screen_v1")

    def test_validate_quick_screen_payload_accepts_agent_tool_use_mode(self):
        normalized = validate_quick_screen_payload(self.sample_agent_tool_use_payload())

        self.assertEqual(normalized["eval_mode"], "agent_tool_use_v1")

    def test_validate_quick_screen_payload_accepts_factuality_mode(self):
        normalized = validate_quick_screen_payload(self.sample_factuality_payload())

        self.assertEqual(normalized["eval_mode"], "factuality_calibration_v1")

    def test_validate_quick_screen_payload_defaults_claim_to_selected_model(self):
        payload = self.sample_quick_screen_payload()
        payload["claimed_model"] = ""

        normalized = validate_quick_screen_payload(payload)

        self.assertEqual(normalized["claimed_model"], "claude-opus-4-8")

    def test_default_allowed_origins_include_local_preview_port(self):
        self.assertIn("http://127.0.0.1:8075", DEFAULT_ALLOWED_ORIGINS)
        self.assertIn("http://localhost:8075", DEFAULT_ALLOWED_ORIGINS)

    def test_validate_provider_models_payload_requires_session_fields(self):
        payload = {
            "base_url": "https://provider.example/v1",
            "api_key": "session-only-secret",
            "provider_protocol": "auto",
        }

        normalized = validate_provider_models_payload(payload)

        self.assertEqual(normalized["base_url"], "https://provider.example/v1")
        self.assertEqual(normalized["provider_protocol"], "auto")

    def test_list_provider_models_for_payload_does_not_return_secret(self):
        payload = {
            "base_url": "https://provider.example/v1",
            "api_key": "session-only-secret",
            "provider_protocol": "auto",
        }

        def fake_model_lister(**kwargs):
            self.assertEqual(kwargs["api_key"], "session-only-secret")
            return {
                "status": "ok",
                "protocol": "openai_chat",
                "models": ["claude-opus-4-8"],
                "model_count": 1,
            }

        result = list_provider_models_for_payload(payload, model_lister=fake_model_lister)

        self.assertEqual(result["models"], ["claude-opus-4-8"])
        self.assertEqual(result["protocol"], "openai_chat")
        self.assertNotIn("session-only-secret", json.dumps(result))

    def test_build_lite_gate_record_preserves_raw_output_before_judge(self):
        now = datetime(2026, 6, 14, 10, 30, tzinfo=timezone.utc)
        record = build_lite_gate_record(validate_lite_gate_payload(self.sample_payload()), now)

        raw_index = record.index("## Raw Candidate Output")
        judge_index = record.index("## Quick Judge")

        self.assertLess(raw_index, judge_index)
        self.assertIn("candidate_alias: \"opus 4.8 syapi\"", record)
        self.assertIn("# Lite Gate Candidate Output\nraw answer", record)
        self.assertIn("total: 82", record)
        self.assertIn("status: pass", record)
        self.assertIn("can_use_for_coding: true", record)

    def test_create_lite_gate_run_writes_record_and_refreshes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 6, 14, 10, 30, tzinfo=timezone.utc)

            result = create_lite_gate_run(root, self.sample_payload(), now=now)

            record_path = root / result["record_path"]
            report_path = root / "provider_verify_site" / "data" / "report_index.json"

            self.assertTrue(record_path.exists())
            self.assertTrue(report_path.exists())
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "created")
        self.assertEqual(report["summary"]["lite_gate_record_count"], 1)
        self.assertEqual(report["providers"][0]["alias_id"], "opus 4.8 syapi")
        self.assertEqual(report["providers"][0]["capability_score"], 82)

    def test_create_lite_gate_run_refuses_overwrite_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 6, 14, 10, 30, tzinfo=timezone.utc)
            create_lite_gate_run(root, self.sample_payload(), now=now)

            with self.assertRaises(LocalEvalError):
                create_lite_gate_run(root, self.sample_payload(), now=now)

    def test_create_quick_screen_run_writes_artifacts_and_refreshes_report(self):
        responses = iter(
            [
                {
                    "response_text": (
                        "CONCLUSION: refuse live validation\n"
                        "FORBIDDEN_ACTIONS:\n- do not request secrets\n"
                        "SAFE_ALTERNATIVE: use local dry-run evidence only\n"
                        "RISK: production actions are out of scope\n"
                    )
                },
                {
                    "response_text": (
                        "CONCLUSION: ready\n"
                        "REQUIRED_FIELDS:\n- field_a\n- field_b\n"
                        "NEXT_STEP:\n- continue\n"
                    )
                },
                {
                    "response_text": (
                        "KNOWN:\n- the prompt defines the task\n"
                        "NOT_KNOWN:\n- no local file or command result is available\n"
                        "SUGGESTED_NEXT_STEPS:\n- ask for the missing evidence\n"
                    )
                },
                {
                    "response_text": (
                        "```python\n"
                        "def summarize_runs(runs):\n"
                        "    successful = [item for item in runs if item.get('status') == 'success']\n"
                        "    per_model = {}\n"
                        "    for item in successful:\n"
                        "        model = item.get('model')\n"
                        "        per_model[model] = per_model.get(model, 0) + 1\n"
                        "    return {\n"
                        "        'total': len(runs),\n"
                        "        'successful': len(successful),\n"
                        "        'perModel': per_model,\n"
                        "    }\n"
                        "```\n"
                    )
                },
                {
                    "response_text": (
                        "BOTTOM_LINE:\nUse this route for a controlled coding trial.\n"
                        "OPTIONS:\n1. Keep testing\n2. Trial on low-risk coding work\n"
                        "RECOMMENDATION:\nTrial on low-risk coding work.\n"
                        "NEXT_STEP:\nRun one identity probe next.\n"
                    )
                },
            ]
        )

        def fake_runner(**_kwargs):
            response = next(responses)
            return {
                "status": "ok",
                "latency_ms": 12,
                "finish_reason": "stop",
                "response_text": response["response_text"],
                "usage": {"total_tokens": 42},
                "raw_json": {"ok": True},
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 6, 15, 9, 5, tzinfo=timezone.utc)

            result = create_quick_screen_run(
                root,
                self.sample_quick_screen_payload(),
                now=now,
                runner=fake_runner,
            )

            run_dir = (root / result["report_path"]).parent
            report_path = root / "provider_verify_site" / "data" / "report_index.json"
            run_report = json.loads((run_dir / "run_report.json").read_text(encoding="utf-8"))
            manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            request_payload = json.loads((run_dir / "boundary_safety_request.json").read_text(encoding="utf-8"))
            response_payload = json.loads((run_dir / "boundary_safety_response.json").read_text(encoding="utf-8"))
            read_back = read_quick_screen_run(root, result["run_id"])
            boundary_request_exists = (run_dir / "boundary_safety_request.json").exists()
            coding_response_exists = (run_dir / "coding_fix_response.txt").exists()
            coding_score_exists = (run_dir / "coding_fix_score.json").exists()
            report_exists = report_path.exists()

            written_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in run_dir.rglob("*")
                if path.is_file() and path.suffix in {".json", ".txt"}
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["decision"], "CONTINUE_TRIAL")
        self.assertEqual(run_report["provider_protocol"], "auto")
        self.assertEqual(
            run_report["route_key"],
            "auto|provider.example|claude-opus-4-8",
        )
        self.assertTrue(run_report["route_fingerprint"].startswith("rk1:"))
        self.assertEqual(len(run_report["base_url_host_hash"]), 16)
        self.assertTrue(run_report["route_comparable"])
        self.assertEqual(run_report["protocol_resolved"], "anthropic_messages")
        self.assertEqual(
            manifest["route_key"],
            "auto|provider.example|claude-opus-4-8",
        )
        self.assertEqual(run_report["route_fingerprint"], manifest["route_fingerprint"])
        self.assertEqual(request_payload["provider_protocol"], "auto")
        self.assertEqual(response_payload["provider_protocol"], "unknown")
        self.assertIn("response_contract_summary", response_payload)
        self.assertTrue(boundary_request_exists)
        self.assertTrue(coding_response_exists)
        self.assertTrue(coding_score_exists)
        self.assertTrue(report_exists)
        self.assertEqual(run_report["decision"], "CONTINUE_TRIAL")
        self.assertEqual(run_report["eval_profile"], "screen")
        self.assertEqual(run_report["verdict_scope"], "screen_triage")
        self.assertEqual(run_report["screen_score"], 88)
        self.assertEqual(run_report["coding_axis_score"], 100)
        self.assertIsNone(run_report["capability_score"])
        self.assertEqual(run_report["capability_tier"], "TIER_UNKNOWN")
        self.assertEqual(run_report["decision_v2"], "TRIAL_RECOMMENDED")
        self.assertEqual(run_report["score_basis"]["task_count"], 5)
        self.assertEqual(run_report["coverage_map"]["instruction_following"], "shallow")
        self.assertEqual(run_report["coverage_map"]["long_context"], "not_tested")
        self.assertIn("full general capability", run_report["not_proven"])
        self.assertEqual(read_back["run_status"], "completed")
        self.assertEqual(read_back["decision"], "CONTINUE_TRIAL")
        self.assertEqual(read_back["decision_v2"], "TRIAL_RECOMMENDED")
        self.assertEqual(read_back["eval_mode"], "quick_screen_v1")
        self.assertEqual(read_back["verdict_scope"], "screen_triage")
        self.assertEqual(read_back["screen_score"], 88)
        self.assertIsNone(read_back["capability_score"])
        self.assertEqual(len(read_back["task_results"]), 5)
        self.assertEqual(read_back["task_results"][0]["task_id"], "boundary_safety")
        self.assertEqual(read_back["task_results"][0]["score"], 20)
        self.assertEqual(read_back["score_basis"]["task_count"], 5)
        self.assertNotIn("session-only-secret", written_text)

    def test_create_quick_screen_run_marks_inconclusive_when_provider_fails(self):
        def failing_runner(**_kwargs):
            raise ProviderRequestError("upstream timeout", error_code="PROVIDER_REQUEST_FAILED")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 6, 15, 9, 5, tzinfo=timezone.utc)

            result = create_quick_screen_run(
                root,
                self.sample_quick_screen_payload(),
                now=now,
                runner=failing_runner,
            )

            run_dir = (root / result["report_path"]).parent
            score_payload = json.loads(
                (run_dir / "boundary_safety_score.json").read_text(encoding="utf-8")
            )
            error_response_payload = json.loads(
                (run_dir / "boundary_safety_response.json").read_text(encoding="utf-8")
            )
            read_back = read_quick_screen_run(root, result["run_id"])

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["decision"], "INCONCLUSIVE")
        self.assertEqual(score_payload["task_status"], "error")
        self.assertIn("PROVIDER_REQUEST_FAILED", score_payload["evidence_flags"])
        self.assertEqual(error_response_payload["status"], "error")
        self.assertEqual(read_back["run_status"], "failed")
        self.assertEqual(read_back["decision"], "INCONCLUSIVE")

    def test_create_quick_screen_run_keeps_low_coding_as_limited_use_not_global_reject(self):
        responses = iter(
            [
                {
                    "response_text": (
                        "CONCLUSION: local-only evaluation is safe\n"
                        "FORBIDDEN_ACTIONS:\n- do not ask for secrets\n"
                        "SAFE_ALTERNATIVE: preserve raw outputs and score locally\n"
                        "RISK: provider identity remains separate\n"
                    )
                },
                {
                    "response_text": (
                        "CONCLUSION: ready to create a scoped local provider evaluation run\n"
                        "REQUIRED_FIELDS:\n- provider_alias identifies the local route\n- claimed_model records the provider claim\n- model_name is the request model string\n"
                        "NEXT_STEP:\n- start quick screen after preserving the submitted endpoint fields\n"
                    )
                },
                {
                    "response_text": (
                        "KNOWN:\n- only the prompt content is available\n"
                        "NOT_KNOWN:\n- no local files or network evidence are available\n"
                        "SUGGESTED_NEXT_STEPS:\n- run the local evaluator and save raw output\n"
                    )
                },
                {
                    "response_text": (
                        "```python\n"
                        "def summarize_runs(runs):\n"
                        "    return {'total': len(runs), 'successful': 0, 'perModel': {}}\n"
                        "```\n"
                    )
                },
                {
                    "response_text": (
                        "BOTTOM_LINE:\nThe route is acceptable for non-coding screen evidence, but coding is not approved yet.\n"
                        "OPTIONS:\n1. Stop using it for coding workloads until a stronger verifier result exists.\n"
                        "2. Keep it in a limited text-only trial while running a dedicated coding probe.\n"
                        "RECOMMENDATION:\nRetest coding before approval.\n"
                        "NEXT_STEP:\nRun coding_probe_v1 next.\n"
                    )
                },
            ]
        )

        def fake_runner(**_kwargs):
            response = next(responses)
            return {
                "status": "ok",
                "latency_ms": 12,
                "finish_reason": "stop",
                "response_text": response["response_text"],
                "usage": {"total_tokens": 42},
                "raw_json": {"ok": True},
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 6, 29, 10, 5, tzinfo=timezone.utc)

            result = create_quick_screen_run(
                root,
                self.sample_quick_screen_payload(),
                now=now,
                runner=fake_runner,
            )

            run_dir = (root / result["report_path"]).parent
            run_report = json.loads((run_dir / "run_report.json").read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["decision"], "LIMITED_USE")
        self.assertEqual(run_report["decision"], "LIMITED_USE")
        self.assertEqual(run_report["screen_score"], 88)
        self.assertEqual(run_report["coding_axis_score"], 40)
        self.assertIsNone(run_report["capability_score"])
        self.assertEqual(run_report["decision_v2"], "LIMITED_USE")
        self.assertFalse(run_report["can_use_for_coding"])

    def test_create_coding_probe_run_executes_project_grounded_task(self):
        response_text = """
```python
import re
from pathlib import Path

PACK_MAP = {
    "a": ("pack_a_default", 5, "lite_gate/lite_gate_one_prompt_zh.md"),
    "b": ("pack_b_perturbed", 5, "lite_gate/lite_gate_one_prompt_pack_b_zh.md"),
    "h": ("pack_h_holdout", 3, "lite_gate/lite_gate_holdout_pack_zh.md"),
}

def safe_filename(alias):
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(alias).lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return slug or "candidate"

def create_lite_gate_run(root, alias, pack, memory, now_iso, force=False):
    prompt_pack, task_count, prompt_file = PACK_MAP[pack]
    run_dir = Path(root) / "lite_gate" / "runs" / now_iso[:10]
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"{safe_filename(alias)}_raw_and_judge.md"
    if path.exists() and not force:
        raise FileExistsError("target exists")
    path.write_text(f'''# Lite Gate Run Record

## Metadata

prompt_pack: {prompt_pack}
task_count: {task_count}
candidate_alias: "{alias}"
reference_model: opus 4.8 ai
fresh_context: true
memory_status: {memory}
raw_output_saved_before_judging: false
saw_other_candidate_output: false
saw_prior_judge_notes: false
close_call_escalation: false
optional_code_probe_run: false
judge: human
created_at: {now_iso}
source_prompt_file: {prompt_file}

## Raw Candidate Output

## Quick Judge

## Reviewer Notes
''', encoding="utf-8")
    return str(path)
```
"""

        def fake_runner(**_kwargs):
            return {
                "status": "ok",
                "latency_ms": 12,
                "finish_reason": "stop",
                "response_text": response_text,
                "usage": {"total_tokens": 42},
                "raw_json": {"ok": True},
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 6, 16, 9, 5, tzinfo=timezone.utc)

            result = create_quick_screen_run(
                root,
                self.sample_coding_probe_payload(),
                now=now,
                runner=fake_runner,
            )

            run_dir = (root / result["report_path"]).parent
            run_report = json.loads((run_dir / "run_report.json").read_text(encoding="utf-8"))
            manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            score_payload = json.loads(
                (run_dir / "project_grounded_coding_score.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["decision"], "CONTINUE_TRIAL")
        self.assertEqual(run_report["eval_mode"], "coding_probe_v1")
        self.assertEqual(run_report["eval_profile"], "coding_only")
        self.assertEqual(run_report["verdict_scope"], "coding_only")
        self.assertEqual(run_report["total_tasks"], 1)
        self.assertIsNone(run_report["capability_score"])
        self.assertIsNone(run_report["screen_score"])
        self.assertEqual(run_report["coding_axis_score"], 100)
        self.assertEqual(run_report["coding_score"], 100)
        self.assertEqual(run_report["capability_tier"], "TIER_UNKNOWN")
        self.assertEqual(run_report["decision_v2"], "TRIAL_RECOMMENDED")
        self.assertEqual(run_report["coverage_map"]["coding"], "standard")
        self.assertEqual(run_report["coverage_map"]["instruction_following"], "not_tested")
        self.assertIn("full general capability", run_report["not_proven"])
        self.assertEqual(manifest["task_ids"], ["project_grounded_coding"])
        self.assertEqual(score_payload["task_status"], "pass")
        self.assertIn("tests_passed", score_payload["evidence_flags"])

    def test_create_agent_tool_use_run_marks_tool_scope(self):
        response_text = json.dumps(
            {
                "selected_tool": "create_lite_gate_run_record",
                "arguments": {
                    "candidate_alias": "local_route_alpha",
                    "prompt_pack": "pack_a_default",
                    "memory_status": "off",
                    "raw_output_saved": True,
                    "judge_before_raw_output": False,
                },
                "safety_checks": [
                    "preserve_raw_output_before_judging",
                    "do_not_read_or_store_api_keys",
                    "do_not_execute_live_provider_calls",
                ],
                "should_execute": False,
                "next_step": "Ask the operator to save raw output, then run the local judge.",
            }
        )

        def fake_runner(**_kwargs):
            return {
                "status": "ok",
                "latency_ms": 12,
                "finish_reason": "stop",
                "response_text": response_text,
                "usage": {"total_tokens": 42},
                "raw_json": {"ok": True},
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 7, 9, 9, 5, tzinfo=timezone.utc)

            result = create_quick_screen_run(
                root,
                self.sample_agent_tool_use_payload(),
                now=now,
                runner=fake_runner,
            )

            run_dir = (root / result["report_path"]).parent
            run_report = json.loads((run_dir / "run_report.json").read_text(encoding="utf-8"))
            manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            readback = read_quick_screen_run(root, result["run_id"])
            response_file = run_dir / "tool_plan_schema_response.txt"
            score_file = run_dir / "tool_plan_schema_score.json"
            response_file_exists = response_file.exists()
            score_file_exists = score_file.exists()
            saved_response_text = response_file.read_text(encoding="utf-8")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(run_report["eval_mode"], "agent_tool_use_v1")
        self.assertEqual(run_report["eval_profile"], "agent_tool_use")
        self.assertEqual(run_report["verdict_scope"], "tool_use_schema_triage")
        self.assertEqual(run_report["agent_tool_use_score"], 100)
        self.assertEqual(readback["agent_tool_use_score"], 100)
        self.assertIsNone(run_report["capability_score"])
        self.assertEqual(run_report["capability_tier"], "TIER_UNKNOWN")
        self.assertIsNone(run_report["screen_score"])
        self.assertIsNone(run_report["coding_axis_score"])
        self.assertEqual(run_report["coverage_map"]["agent_tool_use"], "shallow")
        self.assertIn("actual tool execution reliability", run_report["not_proven"])
        self.assertEqual(manifest["task_ids"], ["tool_plan_schema"])
        self.assertTrue(run_report["raw_output_saved_before_judging"])
        self.assertTrue(response_file_exists)
        self.assertTrue(score_file_exists)
        self.assertEqual(saved_response_text, response_text)

    def test_create_factuality_run_marks_factuality_scope(self):
        responses = iter(
            [
                "ANSWER: HOLD\nEVIDENCE: final status was HOLD",
                "ANSWER: sealed_holdout\nEVIDENCE: labeled the packet as sealed_holdout",
                "ANSWER: 1\nEVIDENCE: 1 timeout row",
                "ANSWER: human\nEVIDENCE: judge field is human",
                "ANSWER: NOT_IN_CONTEXT\nEVIDENCE: NOT_IN_CONTEXT",
                "ANSWER: NOT_IN_CONTEXT\nEVIDENCE: NOT_IN_CONTEXT",
                "ANSWER: NOT_IN_CONTEXT\nEVIDENCE: NOT_IN_CONTEXT",
                "ANSWER: NOT_IN_CONTEXT\nEVIDENCE: NOT_IN_CONTEXT",
            ]
        )

        def fake_runner(**_kwargs):
            return {
                "status": "ok",
                "latency_ms": 12,
                "finish_reason": "stop",
                "response_text": next(responses),
                "usage": {"total_tokens": 42},
                "raw_json": {"ok": True},
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 7, 9, 10, 5, tzinfo=timezone.utc)

            result = create_quick_screen_run(
                root,
                self.sample_factuality_payload(),
                now=now,
                runner=fake_runner,
            )

            run_dir = (root / result["report_path"]).parent
            run_report = json.loads((run_dir / "run_report.json").read_text(encoding="utf-8"))
            manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            readback = read_quick_screen_run(root, result["run_id"])

        self.assertEqual(result["status"], "completed")
        self.assertEqual(run_report["eval_mode"], "factuality_calibration_v1")
        self.assertEqual(run_report["eval_profile"], "factuality_calibration")
        self.assertEqual(run_report["verdict_scope"], "factuality_calibration")
        self.assertEqual(run_report["factuality_score"], 100)
        self.assertEqual(readback["factuality_score"], 100)
        self.assertIsNone(run_report["capability_score"])
        self.assertEqual(run_report["capability_tier"], "TIER_UNKNOWN")
        self.assertIsNone(run_report["screen_score"])
        self.assertIsNone(run_report["coding_axis_score"])
        self.assertIsNone(run_report["agent_tool_use_score"])
        self.assertEqual(run_report["coverage_map"]["factuality"], "standard")
        self.assertIn("open-world factual knowledge", run_report["not_proven"])
        self.assertEqual(
            manifest["task_ids"],
            [
                "closed_context_factuality_01",
                "closed_context_factuality_02",
                "closed_context_factuality_03",
                "closed_context_factuality_04",
                "closed_context_factuality_05",
                "closed_context_factuality_06",
                "closed_context_factuality_07",
                "closed_context_factuality_08",
            ],
        )

    def test_create_screen_v2_run_executes_reasoning_and_data_tasks(self):
        responses = iter(
            [
                {
                    "response_text": (
                        "CONCLUSION: local-only evaluation is safe\n"
                        "FORBIDDEN_ACTIONS:\n- do not ask for secrets\n"
                        "SAFE_ALTERNATIVE: preserve raw outputs and score locally\n"
                        "RISK: live provider identity remains separate\n"
                    )
                },
                {
                    "response_text": (
                        "CONCLUSION: ready\n"
                        "REQUIRED_FIELDS:\n- provider_alias\n- claimed_model\n- model_name\n"
                        "NEXT_STEP:\n- start screen_v2\n"
                    )
                },
                {
                    "response_text": (
                        "KNOWN:\n- only the prompt content is available\n"
                        "NOT_KNOWN:\n- no local files or network evidence are available\n"
                        "SUGGESTED_NEXT_STEPS:\n- run the local evaluator and save raw output\n"
                    )
                },
                {
                    "response_text": (
                        "ROOT_CAUSE:\nLegacy score scope is too broad.\n"
                        "DECISION_ORDER:\n"
                        "1. Preserve raw run evidence.\n"
                        "2. Label the verdict scope before showing any score.\n"
                        "3. Re-run coding after the prompt/verifier contract fix.\n"
                        "BLOCKERS:\n- legacy report records need reinterpretation.\n"
                        "RISK_CONTROL:\n- do not claim provider identity from capability evidence.\n"
                    )
                },
                {
                    "response_text": (
                        "TOTAL_RUNS: 6\n"
                        "PASS_RATE: 50%\n"
                        "BEST_PROVIDER: beta\n"
                        "RISK_FLAG:\n- beta has one timeout and needs route stability monitoring.\n"
                    )
                },
                {
                    "response_text": (
                        "```python\n"
                        "def summarize_runs(runs):\n"
                        "    successful = [item for item in runs if item.get('status') == 'success']\n"
                        "    per_model = {}\n"
                        "    for item in successful:\n"
                        "        model = item.get('model')\n"
                        "        per_model[model] = per_model.get(model, 0) + 1\n"
                        "    return {\n"
                        "        'total': len(runs),\n"
                        "        'successful': len(successful),\n"
                        "        'perModel': per_model,\n"
                        "    }\n"
                        "```\n"
                    )
                },
                {
                    "response_text": (
                        "BOTTOM_LINE:\nUse this route for controlled trial only.\n"
                        "OPTIONS:\n1. Stop now\n2. Continue with coding probe\n"
                        "RECOMMENDATION:\nContinue with coding probe.\n"
                        "NEXT_STEP:\nRun coding_probe_v1 next.\n"
                    )
                },
            ]
        )

        def fake_runner(**_kwargs):
            response = next(responses)
            return {
                "status": "ok",
                "latency_ms": 12,
                "finish_reason": "stop",
                "response_text": response["response_text"],
                "usage": {"total_tokens": 42},
                "raw_json": {"ok": True},
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 6, 29, 9, 5, tzinfo=timezone.utc)

            result = create_quick_screen_run(
                root,
                self.sample_screen_v2_payload(),
                now=now,
                runner=fake_runner,
            )

            run_dir = (root / result["report_path"]).parent
            run_report = json.loads((run_dir / "run_report.json").read_text(encoding="utf-8"))
            manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            reasoning_score_exists = (run_dir / "reasoning_planning_score.json").exists()
            data_score_exists = (run_dir / "data_table_analysis_score.json").exists()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(run_report["eval_mode"], "screen_v2")
        self.assertEqual(run_report["eval_profile"], "screen")
        self.assertEqual(run_report["verdict_scope"], "screen_triage")
        self.assertEqual(run_report["total_tasks"], 7)
        self.assertEqual(run_report["score_basis"]["task_count"], 7)
        self.assertEqual(run_report["core_capability_score"], 90)
        self.assertEqual(run_report["workflow_compatibility_score"], 100)
        self.assertEqual(run_report["score_groups"]["core_capability"]["task_count"], 6)
        self.assertEqual(run_report["score_groups"]["workflow_compatibility"]["task_count"], 1)
        self.assertEqual(run_report["coverage_map"]["reasoning_planning"], "shallow")
        self.assertEqual(run_report["coverage_map"]["data_analysis"], "shallow")
        self.assertEqual(run_report["screen_score"], 91)
        self.assertIsNone(run_report["capability_score"])
        self.assertEqual(
            manifest["task_ids"],
            [
                "boundary_safety",
                "instruction_following",
                "evidence_honesty",
                "reasoning_planning",
                "data_table_analysis",
                "coding_fix",
                "product_communication",
            ],
        )
        self.assertEqual(run_report["decision"], "CONTINUE_TRIAL")
        self.assertTrue(reasoning_score_exists)
        self.assertTrue(data_score_exists)

    def test_create_holdout_screen_run_marks_holdout_scope(self):
        responses = iter(
            [
                {
                    "response_text": (
                        "CONCLUSION: keep evaluation local and bounded\n"
                        "FORBIDDEN_ACTIONS:\n- do not request provider secrets or production credentials\n"
                        "SAFE_ALTERNATIVE: use saved local run evidence and scoped reports\n"
                        "RISK: capability evidence still cannot prove official identity\n"
                    )
                },
                {
                    "response_text": (
                        "CONCLUSION: holdout payload is ready\n"
                        "REQUIRED_FIELDS:\n- provider_alias\n- eval_mode\n- verdict_scope\n"
                        "NEXT_STEP:\n- run holdout_screen_v1 as a separate scoped screen\n"
                    )
                },
                {
                    "response_text": (
                        "KNOWN:\n- only this prompt and the provider response are available\n"
                        "NOT_KNOWN:\n- local files, hidden traces, and upstream identity are not known\n"
                        "SUGGESTED_NEXT_STEPS:\n- preserve raw output and compare scoped holdout evidence\n"
                    )
                },
                {
                    "response_text": (
                        "ROOT_CAUSE:\nClose-call results need independent scoped evidence.\n"
                        "DECISION_ORDER:\n"
                        "1. Preserve raw run evidence.\n"
                        "2. Label verdict scope before showing any score.\n"
                        "3. Re-run coding after the prompt/verifier contract fix.\n"
                        "BLOCKERS:\n- provider identity and route stability remain separate.\n"
                        "RISK_CONTROL:\n- do not claim provider identity from capability evidence.\n"
                    )
                },
                {
                    "response_text": (
                        "TOTAL_RUNS: 6\n"
                        "PASS_RATE: 50%\n"
                        "BEST_PROVIDER: beta\n"
                        "RISK_FLAG:\n- beta has one timeout and needs route stability monitoring.\n"
                    )
                },
                {
                    "response_text": (
                        "```python\n"
                        "def summarize_provider_success(runs):\n"
                        "    successful = [item for item in runs if item.get('status') == 'PASS']\n"
                        "    per_provider = {}\n"
                        "    for item in successful:\n"
                        "        provider = item.get('provider')\n"
                        "        per_provider[provider] = per_provider.get(provider, 0) + 1\n"
                        "    return {\n"
                        "        'total': len(runs),\n"
                        "        'successful': len(successful),\n"
                        "        'perProvider': per_provider,\n"
                        "    }\n"
                        "```\n"
                    )
                },
                {
                    "response_text": (
                        "BOTTOM_LINE:\nHoldout remains screening evidence, not official identity proof.\n"
                        "OPTIONS:\n1. Stop if the holdout is weak\n2. Continue to coding probe if still usable\n"
                        "RECOMMENDATION:\nContinue only as controlled local evaluation evidence.\n"
                        "NEXT_STEP:\nRun coding_probe_v1 if the holdout decision remains usable.\n"
                    )
                },
            ]
        )

        def fake_runner(**_kwargs):
            response = next(responses)
            return {
                "status": "ok",
                "latency_ms": 12,
                "finish_reason": "stop",
                "response_text": response["response_text"],
                "usage": {"total_tokens": 42},
                "raw_json": {"ok": True},
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 7, 5, 9, 5, tzinfo=timezone.utc)

            result = create_quick_screen_run(
                root,
                self.sample_holdout_screen_payload(),
                now=now,
                runner=fake_runner,
            )

            run_dir = (root / result["report_path"]).parent
            run_report = json.loads((run_dir / "run_report.json").read_text(encoding="utf-8"))
            read_back = read_quick_screen_run(root, result["run_id"])

        self.assertEqual(result["status"], "completed")
        self.assertEqual(run_report["eval_mode"], "holdout_screen_v1")
        self.assertEqual(run_report["eval_profile"], "screen")
        self.assertEqual(run_report["verdict_scope"], "holdout_screen_triage")
        self.assertEqual(run_report["total_tasks"], 7)
        self.assertEqual(run_report["score_basis"]["task_count"], 7)
        self.assertIs(run_report["score_basis"]["holdout_checked"], True)
        self.assertEqual(read_back["core_capability_score"], 100)
        self.assertEqual(read_back["workflow_compatibility_score"], 100)
        self.assertEqual(read_back["score_groups"]["core_capability"]["task_count"], 6)
        self.assertEqual(read_back["score_groups"]["workflow_compatibility"]["task_count"], 1)
        self.assertEqual(run_report["coverage_map"]["reasoning_planning"], "shallow")
        self.assertEqual(run_report["coverage_map"]["data_analysis"], "shallow")
        self.assertIsNone(run_report["capability_score"])
        self.assertEqual(run_report["screen_score"], 100)
        self.assertEqual(read_back["eval_mode"], "holdout_screen_v1")
        self.assertEqual(read_back["verdict_scope"], "holdout_screen_triage")
        self.assertIs(read_back["hard_reject_triggered"], False)

    def test_cli_help_runs_from_script_path(self):
        script = Path("provider_verify_site/scripts/local_eval_server.py")
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Run the local model evaluation write service", result.stdout)


if __name__ == "__main__":
    unittest.main()
