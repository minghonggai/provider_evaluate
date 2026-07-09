import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from provider_verify_site.scripts.build_site_data import (
    build_report,
    compute_capability_score,
    compute_capability_tier,
    compute_claim_match_level,
    compute_confidence_breakdown,
    compute_confidence_level,
    compute_context_capability,
    compute_recommended_use,
    compute_task_reliability,
    extract_evidence_flags,
    load_baseline_registry,
    parse_bool,
    write_report,
)


class BuildSiteDataTests(unittest.TestCase):
    def test_parse_bool_normalizes_common_values(self):
        self.assertIs(parse_bool("true"), True)
        self.assertIs(parse_bool("FALSE"), False)
        self.assertIs(parse_bool(" yes "), True)
        self.assertIs(parse_bool("0"), False)
        self.assertIsNone(parse_bool(""))
        self.assertIsNone(parse_bool("unknown"))

    def test_extract_evidence_flags_from_markdown_markers(self):
        text = """
        middleware_in_chain: true
        hard_probe_250k_needle: PASS
        hard_probe_500k_needle: PASS
        thinking_api_structural_passthrough: FAIL
        later_balance_or_quota_403: observed
        """

        flags = extract_evidence_flags(text)

        self.assertIn("middleware_in_chain", flags)
        self.assertIn("hard_probe_250k_needle_pass", flags)
        self.assertIn("hard_probe_500k_needle_pass", flags)
        self.assertIn("thinking_api_structural_passthrough_fail", flags)
        self.assertIn("later_balance_or_quota_403", flags)

    def test_claim_match_keeps_context_capability_separate_from_identity(self):
        provider = {
            "identity_status": "route_unverified",
            "can_claim_true_4_8": False,
            "evidence_flags": ["hard_probe_500k_needle_pass"],
        }

        self.assertEqual(compute_claim_match_level(provider), "unresolved")

    def test_claim_match_handles_verified_and_downgrade_statuses(self):
        self.assertEqual(
            compute_claim_match_level({"identity_status": "identity_verified"}),
            "match_high",
        )
        self.assertEqual(
            compute_claim_match_level({"identity_status": "confirmed_downgrade"}),
            "mismatch_high",
        )

    def test_confidence_level_uses_evidence_depth_not_identity_claim(self):
        lite_gate = {
            "source_type": "lite_gate",
            "evidence_flags": [],
            "code_quality_status": "pass",
            "routing_risk": "not_assessed",
        }
        hard_context = {
            "source_type": "provider_identity_check",
            "identity_status": "route_unverified",
            "evidence_flags": ["hard_probe_500k_needle_pass", "middleware_in_chain"],
            "code_quality_status": "pass",
            "routing_risk": "high",
        }
        verified = {
            "source_type": "provider_identity_check",
            "identity_status": "identity_verified",
            "evidence_flags": ["raw_response_json_received"],
            "code_quality_status": "pass",
            "routing_risk": "low",
        }

        self.assertEqual(compute_confidence_level(lite_gate), "low")
        self.assertEqual(compute_confidence_level(hard_context), "medium")
        self.assertEqual(compute_confidence_level(verified), "verified")

    def test_confidence_breakdown_separates_identity_capability_and_route_stability(self):
        provider = {
            "source_type": "auto_eval_quick_screen",
            "identity_status": "route_unverified",
            "evidence_flags": ["hard_probe_500k_needle_pass"],
            "code_quality_status": "pass",
            "route_comparable": False,
            "task_reliability": "single_run",
        }

        breakdown = compute_confidence_breakdown(provider)

        self.assertEqual(breakdown["identity_confidence"], "low")
        self.assertEqual(breakdown["capability_confidence"], "medium")
        self.assertEqual(breakdown["route_stability_confidence"], "low")

    def test_capability_score_prefers_explicit_quality_scores(self):
        self.assertEqual(
            compute_capability_score(
                {
                    "capability_score": 84,
                    "code_quality_status": "pass",
                }
            ),
            84,
        )
        self.assertEqual(
            compute_capability_score(
                {
                    "code_quality_score": 78,
                    "lite_gate_score": None,
                    "code_quality_status": "pass",
                }
            ),
            78,
        )
        self.assertEqual(
            compute_capability_score(
                {
                    "source_type": "lite_gate",
                    "lite_gate_score": 82,
                    "code_quality_score": 19,
                    "code_quality_status": "pass",
                }
            ),
            82,
        )
        self.assertIsNone(compute_capability_score({"code_quality_status": "unknown"}))

    def test_context_capability_uses_hard_probe_evidence_not_self_report(self):
        self.assertEqual(
            compute_context_capability(
                {
                    "capability_status": "single_session_1m_class_capability_verified",
                    "evidence_flags": ["hard_probe_500k_needle_pass"],
                }
            ),
            "1m_class_observed",
        )
        self.assertEqual(
            compute_context_capability(
                {
                    "capability_status": "unknown",
                    "evidence_flags": ["hard_probe_250k_needle_pass"],
                }
            ),
            "long_context_observed",
        )
        self.assertEqual(
            compute_context_capability({"source_type": "lite_gate", "evidence_flags": []}),
            "not_assessed",
        )

    def test_task_reliability_distinguishes_single_run_from_retry(self):
        self.assertEqual(
            compute_task_reliability(
                {"evidence_flags": ["hard_probe_500k_needle_pass", "hard_probe_500k_retry_pass"]}
            ),
            "single_session_with_retry",
        )
        self.assertEqual(
            compute_task_reliability({"evidence_flags": ["hard_probe_500k_needle_pass"]}),
            "single_session",
        )
        self.assertEqual(
            compute_task_reliability({"source_type": "lite_gate", "lite_gate_score": 82}),
            "single_run",
        )
        self.assertEqual(
            compute_task_reliability(
                {"source_type": "auto_eval_quick_screen", "capability_score": 84}
            ),
            "single_run",
        )

    def test_capability_tier_is_score_based_and_conservative(self):
        self.assertEqual(
            compute_capability_tier({"capability_score": 90, "code_quality_status": "pass"}),
            "TIER_FLAGSHIP_CANDIDATE",
        )
        self.assertEqual(
            compute_capability_tier({"capability_score": 78, "code_quality_status": "pass"}),
            "TIER_STRONG",
        )
        self.assertEqual(
            compute_capability_tier({"capability_score": 62, "code_quality_status": "hold"}),
            "TIER_USABLE",
        )
        self.assertEqual(
            compute_capability_tier({"capability_score": 41, "code_quality_status": "reject"}),
            "TIER_WEAK",
        )
        self.assertEqual(compute_capability_tier({"capability_score": None}), "TIER_UNKNOWN")

    def test_recommended_use_keeps_high_risk_routes_in_trial_mode(self):
        self.assertEqual(
            compute_recommended_use(
                {
                    "capability_tier": "TIER_STRONG",
                    "routing_risk": "high",
                    "identity_status": "route_unverified",
                    "can_use_for_coding": True,
                    "can_use_for_low_risk": True,
                }
            ),
            "coding_trial",
        )
        self.assertEqual(
            compute_recommended_use(
                {
                    "capability_tier": "TIER_STRONG",
                    "routing_risk": "low",
                    "identity_status": "identity_verified",
                    "can_use_for_coding": True,
                    "can_use_for_low_risk": True,
                }
            ),
            "low_risk_use",
        )
        self.assertEqual(
            compute_recommended_use(
                {
                    "capability_tier": "TIER_WEAK",
                    "can_use_for_coding": False,
                }
            ),
            "do_not_use",
        )

    def test_load_baseline_registry_reads_baselines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "provider_verify_site" / "data" / "baseline_registry_v1.json"
            registry.parent.mkdir(parents=True)
            registry.write_text(
                json.dumps(
                    {
                        "version": "baseline_library_v1",
                        "baselines": [
                            {
                                "model_id": "claude-opus-4-8",
                                "display_name": "Claude Opus 4.8",
                                "status": "baseline_missing",
                                "evidence_level": "missing",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            baselines, warnings = load_baseline_registry(root)

        self.assertFalse(warnings)
        self.assertEqual(len(baselines), 1)
        self.assertEqual(baselines[0]["model_id"], "claude-opus-4-8")
        self.assertEqual(baselines[0]["status"], "baseline_missing")

    def test_build_report_reads_provider_identity_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = root / "provider_identity_check" / "runs" / "sample.md"
            record.parent.mkdir(parents=True)
            record.write_text(
                "middleware_in_chain: true\nhard_probe_500k_needle: PASS\n",
                encoding="utf-8",
            )

            index = root / "provider_identity_check" / "results_index.csv"
            index.parent.mkdir(parents=True, exist_ok=True)
            index.write_text(
                "date,provider_id,alias_id,claimed_model,expected_upstream_model,"
                "identity_status,routing_risk,quality_status,code_quality_status,"
                "lite_gate_score,code_quality_score,can_claim_true_4_8,"
                "can_use_for_low_risk,can_use_for_coding,can_replace_reference_model,"
                "decision,latest_record_path,notes\n"
                f"2026-06-06,syapi,opus_4_8syapi,Claude Opus 4.8,claude-opus-4-8,"
                f"route_unverified,high,hold,pass,,78,false,true,true,false,"
                f"continue_audit_restricted_use,{record},sample notes\n",
                encoding="utf-8",
            )

            report = build_report(root)

        self.assertEqual(report["summary"]["total_providers"], 1)
        self.assertEqual(report["summary"]["high_risk_count"], 1)
        self.assertEqual(report["summary"]["coding_allowed_count"], 1)
        self.assertEqual(report["summary"]["coding_ok_count"], 1)
        self.assertEqual(report["summary"]["capability_ok_count"], 1)
        self.assertEqual(report["summary"]["coding_trial_count"], 1)
        self.assertEqual(report["summary"]["needs_more_data_count"], 0)
        provider = report["providers"][0]
        self.assertEqual(provider["provider_id"], "syapi")
        self.assertEqual(provider["alias_id"], "opus_4_8syapi")
        self.assertEqual(provider["claimed_model"], "Claude Opus 4.8")
        self.assertEqual(provider["identity_status"], "route_unverified")
        self.assertEqual(provider["routing_risk"], "high")
        self.assertIs(provider["can_use_for_coding"], True)
        self.assertIn("hard_probe_500k_needle_pass", provider["evidence_flags"])
        self.assertEqual(provider["methodology_version"], "provider_verify_v1")
        self.assertEqual(provider["claim_match_level"], "unresolved")
        self.assertEqual(provider["confidence_level"], "medium")
        self.assertEqual(provider["identity_confidence"], "low")
        self.assertEqual(provider["capability_confidence"], "medium")
        self.assertEqual(provider["route_stability_confidence"], "unknown")
        self.assertEqual(provider["baseline_model_id"], "claude-opus-4-8")
        self.assertEqual(provider["baseline_reference_status"], "baseline_missing")
        self.assertEqual(provider["capability_methodology_version"], "capability_v1")
        self.assertEqual(provider["capability_score"], 78)
        self.assertEqual(provider["coding_score"], 78)
        self.assertEqual(provider["capability_tier"], "TIER_STRONG")
        self.assertEqual(provider["context_capability"], "1m_class_observed")
        self.assertEqual(provider["task_reliability"], "single_session")
        self.assertEqual(provider["recommended_use"], "coding_trial")

    def test_build_report_links_ready_baseline_to_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "provider_verify_site" / "data" / "baseline_registry_v1.json"
            registry.parent.mkdir(parents=True)
            registry.write_text(
                json.dumps(
                    {
                        "version": "baseline_library_v1",
                        "baselines": [
                            {
                                "model_id": "claude-opus-4-8",
                                "display_name": "Claude Opus 4.8",
                                "status": "baseline_ready",
                                "evidence_level": "trusted",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            index = root / "provider_identity_check" / "results_index.csv"
            index.parent.mkdir(parents=True)
            index.write_text(
                "date,provider_id,alias_id,claimed_model,expected_upstream_model,"
                "identity_status,routing_risk,quality_status,code_quality_status,"
                "lite_gate_score,code_quality_score,can_claim_true_4_8,"
                "can_use_for_low_risk,can_use_for_coding,can_replace_reference_model,"
                "decision,latest_record_path,notes\n"
                "2026-06-06,p1,a1,Claude Opus 4.8,claude-opus-4-8,"
                "route_unverified,high,hold,pass,,78,false,true,true,false,"
                "continue,,notes\n",
                encoding="utf-8",
            )

            report = build_report(root)

        provider = report["providers"][0]
        self.assertEqual(report["summary"]["baseline_count"], 1)
        self.assertEqual(report["summary"]["baseline_missing_count"], 0)
        self.assertEqual(provider["baseline_model_id"], "claude-opus-4-8")
        self.assertEqual(provider["baseline_reference_status"], "baseline_ready")

    def test_missing_record_path_adds_warning_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.md"
            index = root / "provider_identity_check" / "results_index.csv"
            index.parent.mkdir(parents=True)
            index.write_text(
                "date,provider_id,alias_id,claimed_model,expected_upstream_model,"
                "identity_status,routing_risk,quality_status,code_quality_status,"
                "lite_gate_score,code_quality_score,can_claim_true_4_8,"
                "can_use_for_low_risk,can_use_for_coding,can_replace_reference_model,"
                "decision,latest_record_path,notes\n"
                f"2026-06-06,p1,a1,Claimed,expected,unknown,unknown,hold,hold,"
                f",,false,false,false,false,needs_more_data,{missing},notes\n",
                encoding="utf-8",
            )

            report = build_report(root)

        self.assertEqual(len(report["providers"]), 1)
        self.assertTrue(report["warnings"])
        self.assertTrue(
            any("missing source" in warning.lower() for warning in report["warnings"])
        )

    def test_build_report_reads_lite_gate_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "lite_gate" / "runs" / "2026-06-07" / "candidate_raw_and_judge.md"
            run.parent.mkdir(parents=True)
            run.write_text(
                """# Lite Gate Run Record

## Metadata

```yaml
eval_type: lite_gate_one_prompt_v2
prompt_pack: pack_a_default
task_count: 5
candidate_alias: "candidate one"
reference_model: opus 4.8 ai
fresh_context: true
memory_status: off
raw_output_saved_before_judging: true
created_at: 2026-06-07T10:00:00+08:00
```

## Quick Judge

```yaml
scores:
  total: 82

code_quality:
  total: 19
  status: pass

label: PASS
can_use_for_coding: true
next_action: "restricted_use"
```
""",
                encoding="utf-8",
            )

            report = build_report(root)

        self.assertEqual(report["summary"]["total_records"], 1)
        self.assertEqual(report["summary"]["lite_gate_record_count"], 1)
        provider = report["providers"][0]
        self.assertEqual(provider["source_type"], "lite_gate")
        self.assertEqual(provider["provider_id"], "candidate one")
        self.assertEqual(provider["alias_id"], "candidate one")
        self.assertEqual(provider["identity_status"], "not_applicable")
        self.assertEqual(provider["quality_status"], "PASS")
        self.assertEqual(provider["code_quality_status"], "pass")
        self.assertEqual(provider["lite_gate_score"], 82)
        self.assertEqual(provider["code_quality_score"], 19)
        self.assertIs(provider["can_use_for_coding"], True)
        self.assertEqual(provider["capability_score"], 82)
        self.assertEqual(provider["coding_score"], 95)
        self.assertEqual(provider["capability_tier"], "TIER_STRONG")
        self.assertEqual(provider["context_capability"], "not_assessed")
        self.assertEqual(provider["task_reliability"], "single_run")
        self.assertEqual(provider["recommended_use"], "coding_trial")

    def test_build_report_skips_lite_gate_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "lite_gate" / "runs" / "2026-06-07"
            runs.mkdir(parents=True)
            (runs / "_template_raw_and_judge.md").write_text(
                "candidate_alias: replace_me\nlabel: PASS | HOLD | REJECT\n",
                encoding="utf-8",
            )
            (runs / "real_raw_and_judge.md").write_text(
                "candidate_alias: real_model\nlabel: HOLD\ncan_use_for_coding: false\n",
                encoding="utf-8",
            )

            report = build_report(root)

        self.assertEqual(report["summary"]["lite_gate_record_count"], 1)
        self.assertEqual(report["providers"][0]["alias_id"], "real_model")

    def test_build_report_reads_auto_eval_run_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_report = (
                root
                / "auto_eval_runs"
                / "2026-06-15"
                / "2026-06-15-090500-opus_4_8syapi"
                / "run_report.json"
            )
            run_report.parent.mkdir(parents=True)
            run_report.write_text(
                json.dumps(
                    {
                        "run_id": "2026-06-15-090500-opus_4_8syapi",
                        "provider_alias": "opus_4_8syapi",
                        "claimed_model": "Claude Opus 4.8",
                        "model_name": "claude-opus-4-8",
                        "provider_protocol": "anthropic_messages",
                        "eval_mode": "quick_screen_v1",
                        "status": "completed",
                        "created_at": "2026-06-15T09:05:00+08:00",
                        "finished_at": "2026-06-15T09:06:00+08:00",
                        "decision": "CONTINUE_TRIAL",
                        "eval_profile": "screen",
                        "verdict_scope": "screen_triage",
                        "screen_score": 84,
                        "capability_score": None,
                        "coding_score": 100,
                        "coding_axis_score": 100,
                        "core_capability_score": 90,
                        "workflow_compatibility_score": 80,
                        "score_groups": {
                            "core_capability": {
                                "score": 90,
                                "earned": 36,
                                "max_score": 40,
                                "task_count": 2,
                            },
                            "workflow_compatibility": {
                                "score": 80,
                                "earned": 16,
                                "max_score": 20,
                                "task_count": 1,
                            },
                        },
                        "capability_tier": "TIER_STRONG",
                        "decision_v2": "TRIAL_RECOMMENDED",
                        "coverage_map": {
                            "instruction_following": "shallow",
                            "coding": "shallow",
                            "long_context": "not_tested"
                        },
                        "score_basis": {
                            "run_count": 1,
                            "task_count": 2
                        },
                        "not_proven": ["full general capability"],
                        "hard_reject_triggered": False,
                        "task_results": [
                            {
                                "task_id": "boundary_safety",
                                "task_status": "pass",
                                "score": 20,
                                "max_score": 20,
                                "hard_reject": False,
                                "evidence_flags": [],
                                "notes": [],
                            },
                            {
                                "task_id": "coding_fix",
                                "task_status": "pass",
                                "score": 20,
                                "max_score": 20,
                                "hard_reject": False,
                                "evidence_flags": ["tests_passed"],
                                "notes": [],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = build_report(root)

        self.assertEqual(report["summary"]["total_records"], 1)
        self.assertEqual(report["summary"]["auto_eval_record_count"], 1)
        provider = report["providers"][0]
        self.assertEqual(provider["source_type"], "auto_eval_quick_screen")
        self.assertEqual(provider["alias_id"], "opus_4_8syapi")
        self.assertEqual(
            provider["route_key"],
            "anthropic_messages|unknown-host|claude-opus-4-8",
        )
        self.assertFalse(provider["route_comparable"])
        self.assertEqual(provider["routing_risk"], "high")
        self.assertEqual(provider["base_url_host_hash"], "unknown")
        self.assertTrue(provider["route_fingerprint"].startswith("uncomparable:"))
        self.assertIn("route_key_unknown_host", provider["evidence_flags"])
        self.assertEqual(provider["claimed_model"], "Claude Opus 4.8")
        self.assertEqual(provider["eval_profile"], "screen")
        self.assertEqual(provider["verdict_scope"], "screen_triage")
        self.assertEqual(provider["screen_score"], 84)
        self.assertIsNone(provider["capability_score"])
        self.assertEqual(provider["coding_axis_score"], 100)
        self.assertEqual(provider["coding_score"], 100)
        self.assertEqual(provider["core_capability_score"], 90)
        self.assertEqual(provider["workflow_compatibility_score"], 80)
        self.assertEqual(provider["score_groups"]["core_capability"]["task_count"], 2)
        self.assertEqual(provider["capability_tier"], "TIER_UNKNOWN")
        self.assertEqual(provider["decision_v2"], "TRIAL_RECOMMENDED")
        self.assertIn("full general capability", provider["not_proven"])
        self.assertEqual(provider["task_reliability"], "single_run")
        self.assertEqual(provider["recommended_use"], "coding_trial")
        self.assertEqual(len(provider["task_results"]), 2)
        self.assertEqual(provider["task_results"][0]["task_id"], "boundary_safety")
        self.assertEqual(provider["task_results"][1]["evidence_flags"], ["tests_passed"])

    def test_build_report_reads_agent_tool_use_score_as_scoped_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_report = (
                root
                / "auto_eval_runs"
                / "2026-07-09"
                / "2026-07-09-090500-agent-tool-route"
                / "run_report.json"
            )
            run_report.parent.mkdir(parents=True)
            run_report.write_text(
                json.dumps(
                    {
                        "run_id": "2026-07-09-090500-agent-tool-route",
                        "provider_alias": "agent_tool_route",
                        "claimed_model": "Claimed Tool Model",
                        "model_name": "claimed-tool-model",
                        "provider_protocol": "openai_chat",
                        "eval_mode": "agent_tool_use_v1",
                        "status": "completed",
                        "created_at": "2026-07-09T09:05:00+08:00",
                        "finished_at": "2026-07-09T09:06:00+08:00",
                        "decision": "CONTINUE_TRIAL",
                        "eval_profile": "agent_tool_use",
                        "verdict_scope": "tool_use_schema_triage",
                        "agent_tool_use_score": 100,
                        "capability_score": None,
                        "screen_score": None,
                        "coding_score": 0,
                        "coding_axis_score": None,
                        "capability_tier": "TIER_UNKNOWN",
                        "decision_v2": "TRIAL_RECOMMENDED",
                        "coverage_map": {
                            "agent_tool_use": "shallow",
                            "route_identity": "not_tested",
                        },
                        "not_proven": [
                            "full general capability",
                            "actual tool execution reliability",
                        ],
                        "hard_reject_triggered": False,
                        "task_results": [
                            {
                                "task_id": "tool_plan_schema",
                                "task_status": "pass",
                                "score": 20,
                                "max_score": 20,
                                "hard_reject": False,
                                "evidence_flags": ["operator_gate_preserved"],
                                "notes": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = build_report(root)

        provider = report["providers"][0]
        self.assertEqual(provider["eval_profile"], "agent_tool_use")
        self.assertEqual(provider["verdict_scope"], "tool_use_schema_triage")
        self.assertEqual(provider["agent_tool_use_score"], 100)
        self.assertIsNone(provider["capability_score"])
        self.assertIsNone(provider["screen_score"])
        self.assertIsNone(provider["coding_axis_score"])
        self.assertEqual(provider["capability_tier"], "TIER_UNKNOWN")
        self.assertEqual(provider["coverage_map"]["agent_tool_use"], "shallow")
        self.assertEqual(provider["score_groups"]["agent_tool_use"]["task_count"], 1)
        self.assertEqual(provider["score_groups"]["agent_tool_use"]["score"], 100)
        self.assertIn("actual tool execution reliability", provider["not_proven"])
        self.assertEqual(provider["task_reliability"], "single_run")

    def test_build_report_reads_factuality_score_as_scoped_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_report = (
                root
                / "auto_eval_runs"
                / "2026-07-09"
                / "2026-07-09-100500-factuality-route"
                / "run_report.json"
            )
            run_report.parent.mkdir(parents=True)
            task_results = [
                {
                    "task_id": f"closed_context_factuality_{index:02d}",
                    "task_status": "pass",
                    "score": 20,
                    "max_score": 20,
                    "hard_reject": False,
                    "evidence_flags": [],
                    "notes": [],
                }
                for index in range(1, 9)
            ]
            run_report.write_text(
                json.dumps(
                    {
                        "run_id": "2026-07-09-100500-factuality-route",
                        "provider_alias": "factuality_route",
                        "claimed_model": "Claimed Factuality Model",
                        "model_name": "claimed-factuality-model",
                        "provider_protocol": "openai_chat",
                        "eval_mode": "factuality_calibration_v1",
                        "status": "completed",
                        "created_at": "2026-07-09T10:05:00+08:00",
                        "finished_at": "2026-07-09T10:06:00+08:00",
                        "decision": "CONTINUE_TRIAL",
                        "eval_profile": "factuality_calibration",
                        "verdict_scope": "factuality_calibration",
                        "factuality_score": 100,
                        "capability_score": None,
                        "screen_score": None,
                        "coding_score": 0,
                        "coding_axis_score": None,
                        "agent_tool_use_score": None,
                        "capability_tier": "TIER_UNKNOWN",
                        "decision_v2": "TRIAL_RECOMMENDED",
                        "coverage_map": {
                            "factuality": "standard",
                            "route_identity": "not_tested",
                        },
                        "not_proven": [
                            "full general capability",
                            "open-world factual knowledge",
                        ],
                        "hard_reject_triggered": False,
                        "task_results": task_results,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = build_report(root)

        provider = report["providers"][0]
        self.assertEqual(provider["eval_profile"], "factuality_calibration")
        self.assertEqual(provider["verdict_scope"], "factuality_calibration")
        self.assertEqual(provider["factuality_score"], 100)
        self.assertIsNone(provider["capability_score"])
        self.assertIsNone(provider["screen_score"])
        self.assertIsNone(provider["coding_axis_score"])
        self.assertEqual(provider["capability_tier"], "TIER_UNKNOWN")
        self.assertEqual(provider["coverage_map"]["factuality"], "standard")
        self.assertEqual(provider["score_groups"]["factuality"]["task_count"], 8)
        self.assertEqual(provider["score_groups"]["factuality"]["score"], 100)
        self.assertIn("open-world factual knowledge", provider["not_proven"])
        self.assertEqual(provider["task_reliability"], "single_run")

    def test_auto_eval_build_reinterprets_legacy_quick_screen_as_screen_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_report = (
                root
                / "auto_eval_runs"
                / "2026-06-29"
                / "2026-06-29-100000-high-score-route"
                / "run_report.json"
            )
            run_report.parent.mkdir(parents=True)
            run_report.write_text(
                json.dumps(
                    {
                        "run_id": "2026-06-29-100000-high-score-route",
                        "provider_alias": "high_score_route",
                        "claimed_model": "Claimed Model",
                        "model_name": "claimed-model",
                        "provider_protocol": "openai_chat",
                        "eval_mode": "quick_screen_v1",
                        "status": "completed",
                        "created_at": "2026-06-29T10:00:00+08:00",
                        "finished_at": "2026-06-29T10:01:00+08:00",
                        "decision": "CONTINUE_TRIAL",
                        "capability_score": 95,
                        "coding_score": 100,
                        "capability_tier": "TIER_STRONG",
                        "hard_reject_triggered": False,
                        "task_results": [
                            {
                                "task_id": "coding_fix",
                                "task_status": "pass",
                                "score": 20,
                                "max_score": 20,
                                "hard_reject": False,
                                "evidence_flags": ["tests_passed"],
                                "notes": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = build_report(root)

        provider = report["providers"][0]
        self.assertEqual(provider["eval_profile"], "screen")
        self.assertEqual(provider["verdict_scope"], "screen_triage")
        self.assertEqual(provider["screen_score"], 95)
        self.assertEqual(provider["coding_axis_score"], 100)
        self.assertIsNone(provider["capability_score"])
        self.assertEqual(provider["capability_tier"], "TIER_UNKNOWN")
        self.assertEqual(provider["decision_v2"], "TRIAL_RECOMMENDED")
        self.assertIn("full general capability", provider["not_proven"])
        self.assertIn("reported_tier=TIER_STRONG", provider["notes"])

    def test_auto_eval_build_preserves_limited_use_screen_with_low_coding_axis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_report = (
                root
                / "auto_eval_runs"
                / "2026-06-29"
                / "2026-06-29-110000-limited-route"
                / "run_report.json"
            )
            run_report.parent.mkdir(parents=True)
            run_report.write_text(
                json.dumps(
                    {
                        "run_id": "2026-06-29-110000-limited-route",
                        "provider_alias": "limited_route",
                        "claimed_model": "Claimed Model",
                        "model_name": "claimed-model",
                        "provider_protocol": "openai_chat",
                        "eval_mode": "quick_screen_v1",
                        "status": "completed",
                        "created_at": "2026-06-29T11:00:00+08:00",
                        "finished_at": "2026-06-29T11:01:00+08:00",
                        "decision": "LIMITED_USE",
                        "eval_profile": "screen",
                        "verdict_scope": "screen_triage",
                        "screen_score": 88,
                        "capability_score": None,
                        "coding_score": 40,
                        "coding_axis_score": 40,
                        "capability_tier": "TIER_UNKNOWN",
                        "decision_v2": "LIMITED_USE",
                        "hard_reject_triggered": False,
                        "can_use_for_coding": False,
                        "task_results": [
                            {
                                "task_id": "boundary_safety",
                                "task_status": "pass",
                                "score": 20,
                                "max_score": 20,
                                "hard_reject": False,
                                "evidence_flags": [],
                                "notes": [],
                            },
                            {
                                "task_id": "coding_fix",
                                "task_status": "fail",
                                "score": 8,
                                "max_score": 20,
                                "hard_reject": False,
                                "evidence_flags": ["tests_failed"],
                                "notes": ["assertion failed"],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = build_report(root)

        provider = report["providers"][0]
        self.assertEqual(provider["eval_profile"], "screen")
        self.assertEqual(provider["screen_score"], 88)
        self.assertEqual(provider["coding_axis_score"], 40)
        self.assertIsNone(provider["capability_score"])
        self.assertEqual(provider["decision"], "LIMITED_USE")
        self.assertEqual(provider["decision_v2"], "LIMITED_USE")
        self.assertEqual(provider["quality_status"], "hold")
        self.assertEqual(provider["code_quality_status"], "reject")
        self.assertEqual(provider["recommended_use"], "needs_more_data")

    def test_auto_eval_build_reinterprets_legacy_reject_from_low_coding_as_limited_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_report = (
                root
                / "auto_eval_runs"
                / "2026-06-29"
                / "2026-06-29-120000-legacy-low-coding"
                / "run_report.json"
            )
            run_report.parent.mkdir(parents=True)
            run_report.write_text(
                json.dumps(
                    {
                        "run_id": "2026-06-29-120000-legacy-low-coding",
                        "provider_alias": "legacy_low_coding",
                        "claimed_model": "Claimed Model",
                        "model_name": "claimed-model",
                        "provider_protocol": "openai_chat",
                        "eval_mode": "quick_screen_v1",
                        "status": "completed",
                        "created_at": "2026-06-29T12:00:00+08:00",
                        "finished_at": "2026-06-29T12:01:00+08:00",
                        "decision": "REJECT_WEAK",
                        "capability_score": 88,
                        "coding_score": 40,
                        "capability_tier": "TIER_WEAK",
                        "hard_reject_triggered": False,
                        "task_results": [
                            {
                                "task_id": "boundary_safety",
                                "task_status": "pass",
                                "score": 20,
                                "max_score": 20,
                                "hard_reject": False,
                                "evidence_flags": [],
                                "notes": [],
                            },
                            {
                                "task_id": "instruction_following",
                                "task_status": "pass",
                                "score": 20,
                                "max_score": 20,
                                "hard_reject": False,
                                "evidence_flags": [],
                                "notes": [],
                            },
                            {
                                "task_id": "evidence_honesty",
                                "task_status": "pass",
                                "score": 20,
                                "max_score": 20,
                                "hard_reject": False,
                                "evidence_flags": [],
                                "notes": [],
                            },
                            {
                                "task_id": "coding_fix",
                                "task_status": "fail",
                                "score": 8,
                                "max_score": 20,
                                "hard_reject": False,
                                "evidence_flags": ["tests_failed"],
                                "notes": ["assertion failed"],
                            },
                            {
                                "task_id": "product_communication",
                                "task_status": "pass",
                                "score": 20,
                                "max_score": 20,
                                "hard_reject": False,
                                "evidence_flags": [],
                                "notes": [],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = build_report(root)

        provider = report["providers"][0]
        self.assertEqual(provider["eval_profile"], "screen")
        self.assertEqual(provider["screen_score"], 88)
        self.assertEqual(provider["coding_axis_score"], 40)
        self.assertEqual(provider["decision"], "LIMITED_USE")
        self.assertEqual(provider["decision_v2"], "LIMITED_USE")
        self.assertEqual(provider["core_capability_score"], 85)
        self.assertEqual(provider["workflow_compatibility_score"], 100)
        self.assertEqual(provider["score_groups"]["core_capability"]["task_count"], 4)
        self.assertEqual(provider["score_groups"]["workflow_compatibility"]["task_count"], 1)
        self.assertIn("legacy_decision_reinterpreted", provider["evidence_flags"])
        self.assertEqual(provider["recommended_use"], "needs_more_data")

    def test_auto_eval_build_reinterprets_legacy_coding_probe_as_coding_axis_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_report = (
                root
                / "auto_eval_runs"
                / "2026-06-29"
                / "2026-06-29-100000-legacy-coding"
                / "run_report.json"
            )
            run_report.parent.mkdir(parents=True)
            run_report.write_text(
                json.dumps(
                    {
                        "run_id": "2026-06-29-100000-legacy-coding",
                        "provider_alias": "legacy_coding",
                        "claimed_model": "Claimed Coding Model",
                        "model_name": "claimed-coding-model",
                        "provider_protocol": "openai_chat",
                        "eval_mode": "coding_probe_v1",
                        "status": "completed",
                        "created_at": "2026-06-29T10:00:00+08:00",
                        "finished_at": "2026-06-29T10:01:00+08:00",
                        "decision": "REJECT_WEAK",
                        "capability_score": 40,
                        "coding_score": 40,
                        "capability_tier": "TIER_WEAK",
                        "hard_reject_triggered": False,
                        "task_results": [
                            {
                                "task_id": "project_grounded_coding",
                                "task_status": "fail",
                                "score": 8,
                                "max_score": 20,
                                "hard_reject": False,
                                "evidence_flags": ["tests_failed"],
                                "notes": ["assertion failed"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = build_report(root)

        provider = report["providers"][0]
        self.assertEqual(provider["eval_profile"], "coding_only")
        self.assertEqual(provider["verdict_scope"], "coding_only")
        self.assertIsNone(provider["screen_score"])
        self.assertEqual(provider["coding_axis_score"], 40)
        self.assertIsNone(provider["capability_score"])
        self.assertEqual(provider["capability_tier"], "TIER_UNKNOWN")
        self.assertEqual(provider["decision_v2"], "RERUN_REQUIRED")
        self.assertEqual(provider["quality_status"], "hold")
        self.assertEqual(provider["code_quality_status"], "unknown")
        self.assertEqual(provider["recommended_use"], "needs_more_data")
        self.assertIn("needs_rerun_after_probe_contract_fix", provider["evidence_flags"])
        self.assertIn("non-coding capability axes", provider["not_proven"])

    def test_build_report_marks_inconclusive_auto_eval_as_needs_more_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_report = (
                root
                / "auto_eval_runs"
                / "2026-06-26"
                / "2026-06-26-100251-codex_current_sakura_gpt55"
                / "run_report.json"
            )
            run_report.parent.mkdir(parents=True)
            run_report.write_text(
                json.dumps(
                    {
                        "run_id": "2026-06-26-100251-codex_current_sakura_gpt55",
                        "provider_alias": "codex_current_sakura_gpt55",
                        "claimed_model": "GPT route from current Codex slot",
                        "model_name": "gpt-5.5",
                        "provider_protocol": "openai_chat",
                        "eval_mode": "quick_screen_v1",
                        "status": "failed",
                        "created_at": "2026-06-26T10:02:51+08:00",
                        "finished_at": "2026-06-26T10:04:00+08:00",
                        "decision": "INCONCLUSIVE",
                        "capability_score": 0,
                        "coding_score": 0,
                        "capability_tier": "TIER_UNKNOWN",
                        "hard_reject_triggered": False,
                        "can_use_for_coding": False,
                        "task_results": [
                            {
                                "task_id": "boundary_safety",
                                "task_status": "pass",
                                "score": 20,
                                "max_score": 20,
                                "hard_reject": False,
                                "evidence_flags": [],
                                "notes": [],
                            },
                            {
                                "task_id": "instruction_following",
                                "task_status": "error",
                                "score": 0,
                                "max_score": 20,
                                "hard_reject": False,
                                "evidence_flags": ["PROVIDER_REQUEST_FAILED"],
                                "notes": ["The read operation timed out"],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = build_report(root)

        provider = report["providers"][0]
        self.assertEqual(provider["decision"], "INCONCLUSIVE")
        self.assertEqual(
            provider["route_key"],
            "openai_chat|unknown-host|gpt-5.5",
        )
        self.assertFalse(provider["route_comparable"])
        self.assertEqual(provider["routing_risk"], "high")
        self.assertIn("route_key_unknown_host", provider["evidence_flags"])
        self.assertEqual(provider["quality_status"], "hold")
        self.assertEqual(provider["code_quality_status"], "unknown")
        self.assertIsNone(provider["capability_score"])
        self.assertIsNone(provider["coding_score"])
        self.assertEqual(provider["capability_tier"], "TIER_UNKNOWN")
        self.assertEqual(provider["recommended_use"], "needs_more_data")

    def test_schema_enums_include_auto_eval_and_status_fields(self):
        schema_path = Path("provider_verify_site/data/schema_v1.json")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        provider_props = schema["properties"]["providers"]["items"]["properties"]

        self.assertIn("auto_eval_quick_screen", provider_props["source_type"]["enum"])
        self.assertIn("route_comparable", provider_props)
        self.assertIn("route_fingerprint", provider_props)
        self.assertIn("base_url_host_hash", provider_props)
        self.assertIn("protocol_resolved", provider_props)
        self.assertIn("route_unverified", provider_props["identity_status"]["enum"])
        self.assertIn("single_session_1m_class_capability_verified", provider_props["capability_status"]["enum"])
        self.assertIn("coding_probe_v1", provider_props["capability_status"]["enum"])
        self.assertIn("screen_v2", provider_props["capability_status"]["enum"])
        self.assertIn("holdout_screen_v1", provider_props["capability_status"]["enum"])
        self.assertIn("high", provider_props["routing_risk"]["enum"])
        self.assertIn("eval_profile", provider_props)
        self.assertIn("verdict_scope", provider_props)
        self.assertIn("screen_score", provider_props)
        self.assertIn("coding_axis_score", provider_props)
        self.assertIn("agent_tool_use_score", provider_props)
        self.assertIn("factuality_score", provider_props)
        self.assertIn("core_capability_score", provider_props)
        self.assertIn("workflow_compatibility_score", provider_props)
        self.assertIn("score_groups", provider_props)
        self.assertIn("agent_tool_use", provider_props["eval_profile"]["enum"])
        self.assertIn("factuality_calibration", provider_props["eval_profile"]["enum"])
        self.assertIn("agent_tool_use_v1", provider_props["capability_status"]["enum"])
        self.assertIn("factuality_calibration_v1", provider_props["capability_status"]["enum"])
        self.assertIn("decision_v2", provider_props)
        self.assertIn("not_proven", provider_props)
        self.assertIn("task_results", provider_props)
        task_props = provider_props["task_results"]["items"]["properties"]
        self.assertIn("task_id", task_props)
        self.assertIn("task_status", task_props)
        self.assertIn("evidence_flags", task_props)

    def test_manifest_marks_agent_tool_use_initial_support(self):
        manifest_path = Path("provider_verify_site/eval_tasks/manifest_v1.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        profiles = {item["profile_id"]: item for item in manifest["profiles"]}
        implemented_tasks = {item["task_id"]: item for item in manifest["implemented_tasks"]}
        planned_tasks = {item["task_id"]: item for item in manifest["planned_tasks"]}

        self.assertEqual(profiles["agent_tool_use_v1"]["status"], "implemented_initial")
        self.assertEqual(profiles["agent_tool_use_v1"]["task_count"], 1)
        self.assertIn("tool_plan_schema", implemented_tasks)
        self.assertEqual(implemented_tasks["tool_plan_schema"]["profile_id"], "agent_tool_use_v1")
        self.assertEqual(implemented_tasks["tool_plan_schema"]["scorer"], "tool_plan_schema")
        self.assertEqual(planned_tasks["tool_plan_schema_v1"]["status"], "implemented_initial")
        self.assertEqual(planned_tasks["tool_plan_schema_v1"]["implemented_as"], "tool_plan_schema")

    def test_manifest_marks_factuality_calibration_initial_support(self):
        manifest_path = Path("provider_verify_site/eval_tasks/manifest_v1.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        profiles = {item["profile_id"]: item for item in manifest["profiles"]}
        implemented_tasks = {item["task_id"]: item for item in manifest["implemented_tasks"]}
        planned_tasks = {item["task_id"]: item for item in manifest["planned_tasks"]}

        self.assertEqual(profiles["factuality_calibration_v1"]["status"], "implemented_initial")
        self.assertEqual(profiles["factuality_calibration_v1"]["task_count"], 8)
        self.assertEqual(profiles["factuality_calibration_v1"]["score_scope"], "factuality_score")
        self.assertIs(profiles["factuality_calibration_v1"]["may_emit_capability_score"], False)
        for index in range(1, 9):
            task_id = f"closed_context_factuality_{index:02d}"
            self.assertIn(task_id, implemented_tasks)
            self.assertEqual(implemented_tasks[task_id]["profile_id"], "factuality_calibration_v1")
            self.assertEqual(implemented_tasks[task_id]["scorer"], "closed_context_factuality")
            self.assertEqual(implemented_tasks[task_id]["score_role"], "factuality_score")
        self.assertEqual(planned_tasks["closed_context_factuality_v1"]["status"], "implemented_initial")
        self.assertEqual(
            planned_tasks["closed_context_factuality_v1"]["implemented_as"],
            [f"closed_context_factuality_{index:02d}" for index in range(1, 9)],
        )

    def test_write_report_creates_json_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "provider_verify_site" / "data" / "report_index.json"

            write_report(root, output)

            data = json.loads(output.read_text(encoding="utf-8"))

        self.assertIn("generated_at", data)
        self.assertIn("providers", data)
        self.assertIn("summary", data)

    def test_cli_help_runs_from_script_path(self):
        script = Path("provider_verify_site/scripts/build_site_data.py")
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Build Provider Verify static site data", result.stdout)


if __name__ == "__main__":
    unittest.main()
