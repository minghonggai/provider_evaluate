import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from provider_verify_site.scripts.auto_eval_storage import build_route_metadata


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off"}
KNOWN_PATH_MARKERS = (
    "provider_identity_check",
    "provider_4_8_comparison",
    "lite_gate",
    "auto_eval_runs",
    "eval_runs",
)
CAPABILITY_TIERS_STRONG_OR_BETTER = {"TIER_FLAGSHIP_CANDIDATE", "TIER_STRONG"}
PROVIDER_FAILURE_CODES = {
    "PROVIDER_REQUEST_FAILED",
    "TIMEOUT",
    "NETWORK_ERROR",
    "AUTH_BLOCKED",
    "QUOTA_BLOCKED",
    "CONTRACT_INVALID",
    "PROVIDER_PROTOCOL_UNSUPPORTED",
    "TASK_RUNNER_ERROR",
}
SCOPED_EVAL_PROFILES = {"screen", "coding_only", "agent_tool_use"}
SCREEN_EVAL_MODES = {"quick_screen_v1", "screen_v2", "holdout_screen_v1"}
CODING_ONLY_EVAL_MODES = {"coding_probe_v1"}
AGENT_TOOL_USE_EVAL_MODES = {"agent_tool_use_v1"}
CORE_CAPABILITY_TASK_IDS = {
    "boundary_safety",
    "instruction_following",
    "evidence_honesty",
    "reasoning_planning",
    "data_table_analysis",
    "coding_fix",
    "project_grounded_coding",
}
WORKFLOW_COMPATIBILITY_TASK_IDS = {"product_communication"}
CODING_TASK_IDS = {"coding_fix", "project_grounded_coding"}
AGENT_TOOL_USE_TASK_IDS = {"tool_plan_schema"}


def parse_bool(value):
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return None


def clean_value(value, default=""):
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


def infer_eval_profile(payload):
    explicit = clean_value(payload.get("eval_profile"))
    if explicit:
        return explicit
    eval_mode = clean_value(payload.get("eval_mode"))
    if eval_mode in SCREEN_EVAL_MODES:
        return "screen"
    if eval_mode in CODING_ONLY_EVAL_MODES:
        return "coding_only"
    if eval_mode in AGENT_TOOL_USE_EVAL_MODES:
        return "agent_tool_use"
    return "unknown"


def infer_verdict_scope(payload, eval_profile):
    explicit = clean_value(payload.get("verdict_scope"))
    if explicit:
        return explicit
    if eval_profile == "screen":
        return "screen_triage"
    if eval_profile == "coding_only":
        return "coding_only"
    if eval_profile == "agent_tool_use":
        return "tool_use_schema_triage"
    return "unknown"


def score_group_from_tasks(task_results, task_ids):
    group_results = [item for item in task_results or [] if item.get("task_id") in task_ids]
    earned = sum(int_or_none(item.get("score")) or 0 for item in group_results)
    possible = sum(int_or_none(item.get("max_score")) or 20 for item in group_results)
    return {
        "score": int(round((earned / possible) * 100)) if possible else None,
        "earned": earned,
        "max_score": possible,
        "task_count": len(group_results),
    }


def score_groups_from_tasks(task_results):
    return {
        "core_capability": score_group_from_tasks(task_results, CORE_CAPABILITY_TASK_IDS),
        "workflow_compatibility": score_group_from_tasks(task_results, WORKFLOW_COMPATIBILITY_TASK_IDS),
        "coding": score_group_from_tasks(task_results, CODING_TASK_IDS),
        "agent_tool_use": score_group_from_tasks(task_results, AGENT_TOOL_USE_TASK_IDS),
    }


def base_coverage_map():
    return {
        "boundary_safety": "not_tested",
        "instruction_following": "not_tested",
        "evidence_honesty": "not_tested",
        "reasoning_planning": "not_tested",
        "data_analysis": "not_tested",
        "coding": "not_tested",
        "product_communication": "not_tested",
        "agent_tool_use": "not_tested",
        "external_research": "not_tested",
        "long_context": "not_tested",
        "route_identity": "not_tested",
        "route_stability": "not_tested",
    }


def default_coverage_map(eval_mode):
    coverage = base_coverage_map()
    if eval_mode in SCREEN_EVAL_MODES:
        coverage.update(
            {
                "boundary_safety": "shallow",
                "instruction_following": "shallow",
                "evidence_honesty": "shallow",
                "coding": "shallow",
                "product_communication": "shallow",
            }
        )
        if eval_mode in {"screen_v2", "holdout_screen_v1"}:
            coverage["reasoning_planning"] = "shallow"
            coverage["data_analysis"] = "shallow"
    elif eval_mode in CODING_ONLY_EVAL_MODES:
        coverage["coding"] = "standard"
    elif eval_mode in AGENT_TOOL_USE_EVAL_MODES:
        coverage["agent_tool_use"] = "shallow"
    return coverage


def default_not_proven(eval_mode):
    if eval_mode in SCREEN_EVAL_MODES:
        return [
            "full general capability",
            "provider identity",
            "long-context capability",
            "multi-session route stability",
            "production suitability",
        ]
    if eval_mode in CODING_ONLY_EVAL_MODES:
        return [
            "full general capability",
            "provider identity",
            "long-context capability",
            "multi-session route stability",
            "non-coding capability axes",
        ]
    if eval_mode in AGENT_TOOL_USE_EVAL_MODES:
        return [
            "full general capability",
            "actual tool execution reliability",
            "provider identity",
            "live-system safety",
            "long-context capability",
            "multi-session route stability",
        ]
    return []


def default_decision_v2(
    eval_mode,
    run_status,
    hard_reject,
    screen_score,
    coding_axis_score,
    agent_tool_use_score=None,
):
    if run_status != "completed":
        return "INCONCLUSIVE"
    if hard_reject:
        return "REJECTED"
    if eval_mode in SCREEN_EVAL_MODES:
        score = int(screen_score or 0)
        coding = int(coding_axis_score or 0)
        if score >= 80 and coding >= 60:
            return "TRIAL_RECOMMENDED"
        if score >= 65:
            return "LIMITED_USE"
        return "NOT_RECOMMENDED"
    if eval_mode in CODING_ONLY_EVAL_MODES:
        score = int(coding_axis_score or 0)
        if score >= 85:
            return "TRIAL_RECOMMENDED"
        if score >= 70:
            return "LIMITED_USE"
        return "NOT_RECOMMENDED"
    if eval_mode in AGENT_TOOL_USE_EVAL_MODES:
        score = int(agent_tool_use_score or 0)
        if score >= 85:
            return "TRIAL_RECOMMENDED"
        if score >= 70:
            return "LIMITED_USE"
        return "NOT_RECOMMENDED"
    return "unknown"


def quality_status_from_decision(decision):
    if decision == "CONTINUE_TRIAL":
        return "pass"
    if decision in {"LIMITED_USE", "NEEDS_MORE_DATA", "INCONCLUSIVE"}:
        return "hold"
    if decision == "REJECT_WEAK":
        return "reject"
    return "unknown"


def has_legacy_coding_probe_contract_mismatch(eval_mode, explicit_eval_profile, task_results):
    if eval_mode not in CODING_ONLY_EVAL_MODES or explicit_eval_profile:
        return False
    for task in task_results:
        if task.get("task_id") != "project_grounded_coding":
            continue
        notes = [clean_value(note).lower() for note in (task.get("notes") or [])]
        if any(note == "assertion failed" for note in notes):
            return True
    return False


def derive_route_key(provider_protocol, base_url, model_name):
    return build_route_metadata(provider_protocol, base_url, model_name)["route_key"]


def short_hash(value):
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


def route_metadata_from_payload(payload):
    metadata = build_route_metadata(
        payload.get("provider_protocol"),
        payload.get("base_url"),
        payload.get("model_name"),
        payload.get("protocol_resolved"),
    )
    route_key = clean_value(payload.get("route_key"))
    if route_key:
        metadata["route_key"] = route_key
        parts = route_key.split("|")
        if len(parts) >= 3:
            host = clean_value(parts[1], "unknown-host").lower()
            metadata["route_comparable"] = host != "unknown-host"
            metadata["base_url_host_hash"] = (
                short_hash(host) if metadata["route_comparable"] else "unknown"
            )
            prefix = "rk1" if metadata["route_comparable"] else "uncomparable"
            metadata["route_fingerprint"] = f"{prefix}:{short_hash(route_key)}"

    for field in [
        "route_fingerprint",
        "base_url_host_hash",
        "protocol_resolved",
        "route_comparable",
    ]:
        if field in payload:
            metadata[field] = payload[field]
    return metadata


def extract_evidence_flags(text):
    text = text or ""
    lowered = text.lower()
    checks = {
        "middleware_in_chain": [
            "middleware_in_chain: true",
            "oneapi / newapi",
            "x-oneapi-request-id",
        ],
        "hard_probe_250k_needle_pass": ["hard_probe_250k_needle: pass", "250k tokens needle-in-haystack: pass"],
        "hard_probe_500k_needle_pass": ["hard_probe_500k_needle: pass", "500k tokens needle-in-haystack: pass"],
        "hard_probe_500k_retry_pass": ["hard_probe_500k_retry_original_wording: pass", "500k retry pass"],
        "thinking_api_structural_passthrough_fail": ["thinking_api_structural_passthrough: fail"],
        "later_balance_or_quota_403": ["later_balance_or_quota_403: observed", "403 precharge balance"],
        "raw_response_json_received": ["raw_response_json_received: true"],
        "fallback_policy_not_visible": ["fallback_policy_visible: false", "fallback_enabled: unknown"],
    }
    flags = []
    for flag, needles in checks.items():
        if any(needle in lowered for needle in needles):
            flags.append(flag)
    return flags


def extract_yaml_like_value(text, key):
    if not text:
        return ""
    pattern = rf"^\s*{re.escape(key)}\s*:\s*['\"]?([^'\"\r\n#]+)"
    match = re.search(pattern, text, flags=re.MULTILINE)
    if not match:
        return ""
    return match.group(1).strip()


def extract_nested_yaml_value(text, section, key):
    if not text:
        return ""
    section_pattern = rf"^\s*{re.escape(section)}\s*:\s*\n(?P<body>(?:\s{{2,}}[^\n]*\n?)*)"
    section_match = re.search(section_pattern, text, flags=re.MULTILINE)
    if not section_match:
        return ""
    body = section_match.group("body")
    return extract_yaml_like_value(body, key)


def normalize_quoted_value(value):
    value = clean_value(value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def resolve_source_path(root, raw_path):
    root = Path(root)
    raw_path = clean_value(raw_path)
    if not raw_path:
        return None

    direct = Path(raw_path)
    if direct.exists():
        return direct

    normalized = raw_path.replace("\\", "/")
    for marker in KNOWN_PATH_MARKERS:
        marker_pos = normalized.find(marker)
        if marker_pos >= 0:
            candidate = root / normalized[marker_pos:]
            if candidate.exists():
                return candidate
            return candidate

    candidate = root / raw_path
    return candidate


def read_text_if_exists(path, warnings, label):
    if not path:
        return ""
    if not path.exists():
        warnings.append(f"Missing source for {label}: {path}")
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def display_path(root, path):
    if not path:
        return ""
    root = Path(root)
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        normalized = str(path).replace("\\", "/")
        for marker in KNOWN_PATH_MARKERS:
            marker_pos = normalized.find(marker)
            if marker_pos >= 0:
                return normalized[marker_pos:]
        return str(path)


def int_or_none(value):
    value = clean_value(value)
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def clamp_score(value):
    value = int_or_none(value)
    if value is None:
        return None
    return max(0, min(100, value))


def extract_task_result_flags(task_results):
    flags = []
    for item in task_results or []:
        if not isinstance(item, dict):
            continue
        flags.extend(item.get("evidence_flags") or [])
    return sorted(set(flags))


def normalize_task_results(task_results):
    normalized = []
    for item in task_results or []:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "task_id": clean_value(item.get("task_id"), "unknown"),
                "task_status": clean_value(item.get("task_status"), "unknown"),
                "score": int_or_none(item.get("score")),
                "max_score": int_or_none(item.get("max_score")),
                "hard_reject": bool(item.get("hard_reject")),
                "evidence_flags": list(item.get("evidence_flags") or []),
                "notes": list(item.get("notes") or []),
            }
        )
    return normalized


def compute_claim_match_level(provider):
    identity_status = clean_value(provider.get("identity_status"), "unknown")
    if identity_status == "identity_verified":
        return "match_high"
    if identity_status == "confirmed_downgrade":
        return "mismatch_high"
    if provider.get("can_claim_true_4_8") is True:
        return "match_medium"
    if identity_status in {"route_unverified", "identity_unverified", "unknown"}:
        return "unresolved"
    if identity_status == "not_applicable":
        return "not_applicable"
    return "unresolved"


def compute_confidence_level(provider):
    identity_status = clean_value(provider.get("identity_status"), "unknown")
    if identity_status == "identity_verified":
        return "verified"
    if identity_status == "confirmed_downgrade":
        return "high"

    flags = set(provider.get("evidence_flags") or [])
    hard_context = bool(
        {"hard_probe_500k_needle_pass", "hard_probe_500k_retry_pass"} & flags
    )
    has_middleware_evidence = "middleware_in_chain" in flags
    has_code_pass = provider.get("code_quality_status") == "pass"

    if hard_context and has_middleware_evidence:
        return "medium"
    if hard_context or has_code_pass:
        return "low"
    return "low"


def compute_confidence_breakdown(provider):
    identity_status = clean_value(provider.get("identity_status"), "unknown")
    flags = set(provider.get("evidence_flags") or [])
    hard_context = bool(
        {"hard_probe_500k_needle_pass", "hard_probe_500k_retry_pass"} & flags
    )

    if identity_status == "identity_verified":
        identity_confidence = "verified"
    elif identity_status == "confirmed_downgrade":
        identity_confidence = "high"
    elif identity_status == "not_applicable":
        identity_confidence = "not_applicable"
    elif identity_status in {"route_unverified", "identity_unverified", "unknown"}:
        identity_confidence = "low"
    else:
        identity_confidence = "unknown"

    if "hard_probe_500k_retry_pass" in flags:
        capability_confidence = "high"
    elif hard_context or provider.get("code_quality_status") == "pass":
        capability_confidence = "medium"
    elif provider.get("capability_score") is not None:
        capability_confidence = "low"
    else:
        capability_confidence = "unknown"

    if "route_comparable" not in provider:
        route_stability_confidence = "unknown"
    elif provider.get("route_comparable") is False:
        route_stability_confidence = "low"
    elif provider.get("task_reliability") == "single_session_with_retry":
        route_stability_confidence = "medium"
    elif provider.get("task_reliability") in {"single_session", "single_run"}:
        route_stability_confidence = "low"
    else:
        route_stability_confidence = "unknown"

    return {
        "identity_confidence": identity_confidence,
        "capability_confidence": capability_confidence,
        "route_stability_confidence": route_stability_confidence,
    }


def compute_coding_score(provider):
    explicit_axis_score = int_or_none(provider.get("coding_axis_score"))
    if explicit_axis_score is not None:
        return clamp_score(explicit_axis_score)

    explicit_coding_score = int_or_none(provider.get("coding_score"))
    if explicit_coding_score is not None:
        return clamp_score(explicit_coding_score)

    explicit_score = int_or_none(provider.get("code_quality_score"))
    if explicit_score is not None:
        if provider.get("source_type") == "lite_gate" and explicit_score <= 20:
            return clamp_score(explicit_score * 5)
        return clamp_score(explicit_score)

    status = clean_value(provider.get("code_quality_status"), "unknown").lower()
    if status == "pass":
        return 75
    if status == "hold":
        return 55
    if status == "reject":
        return 30
    return None


def compute_capability_score(provider):
    if clean_value(provider.get("eval_profile")) in SCOPED_EVAL_PROFILES:
        return None

    explicit_capability_score = int_or_none(provider.get("capability_score"))
    if explicit_capability_score is not None:
        return clamp_score(explicit_capability_score)

    lite_gate_score = int_or_none(provider.get("lite_gate_score"))
    if provider.get("source_type") == "lite_gate" and lite_gate_score is not None:
        return clamp_score(lite_gate_score)

    explicit_code_score = int_or_none(provider.get("code_quality_score"))
    if explicit_code_score is not None:
        return clamp_score(explicit_code_score)

    if lite_gate_score is not None:
        return clamp_score(lite_gate_score)

    code_status = clean_value(provider.get("code_quality_status"), "unknown").lower()
    quality_status = clean_value(provider.get("quality_status"), "unknown").lower()
    can_code = provider.get("can_use_for_coding")
    flags = set(provider.get("evidence_flags") or [])
    has_hard_context = bool(
        {"hard_probe_500k_needle_pass", "hard_probe_500k_retry_pass"} & flags
    )

    if code_status == "pass" and can_code is True:
        return 75
    if code_status == "pass" or (has_hard_context and quality_status != "reject"):
        return 70
    if code_status == "hold" or quality_status == "hold":
        return 55
    if code_status == "reject" or quality_status == "reject":
        return 30
    return None


def compute_context_capability(provider):
    flags = set(provider.get("evidence_flags") or [])
    capability_status = clean_value(provider.get("capability_status"), "unknown").lower()
    if (
        {"hard_probe_500k_needle_pass", "hard_probe_500k_retry_pass"} & flags
        or "1m_class_capability_verified" in capability_status
    ):
        return "1m_class_observed"
    if "hard_probe_250k_needle_pass" in flags:
        return "long_context_observed"
    if provider.get("source_type") in {"lite_gate", "auto_eval_quick_screen"}:
        return "not_assessed"
    return "standard_context_or_unknown"


def compute_task_reliability(provider):
    flags = set(provider.get("evidence_flags") or [])
    if "hard_probe_500k_retry_pass" in flags:
        return "single_session_with_retry"
    if {"hard_probe_500k_needle_pass", "hard_probe_250k_needle_pass"} & flags:
        return "single_session"
    if provider.get("source_type") in {"lite_gate", "auto_eval_quick_screen"} and (
        provider.get("lite_gate_score") is not None
        or provider.get("screen_score") is not None
        or provider.get("coding_axis_score") is not None
        or provider.get("agent_tool_use_score") is not None
        or provider.get("code_quality_score") is not None
        or provider.get("capability_score") is not None
        or provider.get("coding_score") is not None
        or clean_value(provider.get("quality_status"), "unknown") != "unknown"
    ):
        return "single_run"
    return "unknown"


def compute_capability_tier(provider):
    if clean_value(provider.get("eval_profile")) in SCOPED_EVAL_PROFILES:
        return "TIER_UNKNOWN"

    explicit_tier = clean_value(provider.get("capability_tier"))
    if provider.get("source_type") == "auto_eval_quick_screen" and explicit_tier in {
        "TIER_FLAGSHIP_CANDIDATE",
        "TIER_STRONG",
        "TIER_USABLE",
        "TIER_WEAK",
        "TIER_UNKNOWN",
    }:
        return explicit_tier

    score = int_or_none(provider.get("capability_score"))
    if score is None:
        score = compute_capability_score(provider)
    if score is None:
        return "TIER_UNKNOWN"

    code_status = clean_value(provider.get("code_quality_status"), "unknown").lower()
    quality_status = clean_value(provider.get("quality_status"), "unknown").lower()
    if code_status == "reject" or quality_status == "reject":
        return "TIER_WEAK"
    if score >= 90 and code_status in {"pass", "unknown"}:
        return "TIER_FLAGSHIP_CANDIDATE"
    if score >= 75:
        return "TIER_STRONG"
    if score >= 60:
        return "TIER_USABLE"
    return "TIER_WEAK"


def compute_recommended_use(provider):
    decision_v2 = clean_value(provider.get("decision_v2"))
    if decision_v2 == "TRIAL_RECOMMENDED":
        return "coding_trial"
    if decision_v2 == "LIMITED_USE":
        return "needs_more_data"
    if decision_v2 == "RERUN_REQUIRED":
        return "needs_more_data"
    if decision_v2 in {"NOT_RECOMMENDED", "REJECTED"}:
        return "do_not_use"

    tier = clean_value(provider.get("capability_tier"), "TIER_UNKNOWN")
    can_code = provider.get("can_use_for_coding")
    routing_risk = clean_value(provider.get("routing_risk"), "unknown")
    identity_status = clean_value(provider.get("identity_status"), "unknown")

    if can_code is False or tier == "TIER_WEAK":
        return "do_not_use"
    if tier == "TIER_UNKNOWN":
        return "needs_more_data"
    if tier == "TIER_USABLE":
        return "needs_more_data"

    if tier in CAPABILITY_TIERS_STRONG_OR_BETTER:
        if provider.get("source_type") == "lite_gate":
            return "coding_trial"
        if routing_risk == "high" or identity_status in {
            "route_unverified",
            "identity_unverified",
            "unknown",
        }:
            return "coding_trial"
        if provider.get("can_use_for_low_risk") is True:
            return "low_risk_use"
        return "coding_trial"

    return "needs_more_data"


def apply_methodology(provider):
    if provider.get("source_type") == "auto_eval_quick_screen" and clean_value(
        provider.get("decision"), "unknown"
    ) == "INCONCLUSIVE":
        provider["methodology_version"] = "provider_verify_v1"
        provider["claim_match_level"] = compute_claim_match_level(provider)
        provider["confidence_level"] = compute_confidence_level(provider)
        provider["capability_methodology_version"] = "capability_v1"
        provider["capability_score"] = None
        provider["coding_score"] = None
        provider["context_capability"] = compute_context_capability(provider)
        provider["task_reliability"] = compute_task_reliability(provider)
        provider["capability_tier"] = "TIER_UNKNOWN"
        provider["recommended_use"] = "needs_more_data"
        provider.update(compute_confidence_breakdown(provider))
        return provider

    provider["methodology_version"] = "provider_verify_v1"
    provider["claim_match_level"] = compute_claim_match_level(provider)
    provider["confidence_level"] = compute_confidence_level(provider)
    provider["capability_methodology_version"] = "capability_v1"
    provider["capability_score"] = compute_capability_score(provider)
    provider["coding_score"] = compute_coding_score(provider)
    provider["context_capability"] = compute_context_capability(provider)
    provider["task_reliability"] = compute_task_reliability(provider)
    provider["capability_tier"] = compute_capability_tier(provider)
    provider["recommended_use"] = compute_recommended_use(provider)
    provider.update(compute_confidence_breakdown(provider))
    return provider


def load_baseline_registry(root):
    registry_path = Path(root) / "provider_verify_site" / "data" / "baseline_registry_v1.json"
    if not registry_path.exists():
        return [], [f"Missing baseline registry: {registry_path}"]
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [], [f"Invalid baseline registry JSON: {registry_path}: {exc}"]
    baselines = data.get("baselines", [])
    if not isinstance(baselines, list):
        return [], [f"Invalid baseline registry shape: baselines must be a list: {registry_path}"]
    normalized = []
    for item in baselines:
        if not isinstance(item, dict):
            continue
        model_id = clean_value(item.get("model_id"))
        if not model_id:
            continue
        normalized.append(
            {
                "model_id": model_id,
                "display_name": clean_value(item.get("display_name"), model_id),
                "status": clean_value(item.get("status"), "baseline_missing"),
                "evidence_level": clean_value(item.get("evidence_level"), "missing"),
                "source_paths": item.get("source_paths", []),
                "notes": clean_value(item.get("notes")),
            }
        )
    return normalized, []


def attach_baseline_reference(provider, baselines_by_id):
    expected = clean_value(provider.get("expected_upstream_model"), "unknown")
    if expected in {"unknown", "not_applicable"}:
        provider["baseline_model_id"] = "not_applicable"
        provider["baseline_reference_status"] = "not_applicable"
        return provider

    baseline = baselines_by_id.get(expected)
    provider["baseline_model_id"] = expected
    provider["baseline_reference_status"] = (
        clean_value(baseline.get("status"), "baseline_missing")
        if baseline
        else "baseline_missing"
    )
    return provider


def provider_from_identity_row(root, row, warnings):
    record_path = resolve_source_path(root, row.get("latest_record_path", ""))
    record_text = read_text_if_exists(
        record_path,
        warnings,
        clean_value(row.get("alias_id"), "unknown provider"),
    )
    notes = clean_value(row.get("notes"))
    combined_text = "\n".join([record_text, notes])
    capability_status = extract_yaml_like_value(combined_text, "capability_status")
    if not capability_status and "single_session_1m_class_capability_verified" in combined_text:
        capability_status = "single_session_1m_class_capability_verified"

    return apply_methodology({
        "source_type": "provider_identity_check",
        "date": clean_value(row.get("date")),
        "provider_id": clean_value(row.get("provider_id"), "unknown"),
        "alias_id": clean_value(row.get("alias_id"), "unknown"),
        "claimed_model": clean_value(row.get("claimed_model"), "unknown"),
        "expected_upstream_model": clean_value(row.get("expected_upstream_model"), "unknown"),
        "identity_status": clean_value(row.get("identity_status"), "unknown"),
        "capability_status": clean_value(capability_status, "unknown"),
        "quality_status": clean_value(row.get("quality_status"), "unknown"),
        "code_quality_status": clean_value(row.get("code_quality_status"), "unknown"),
        "routing_risk": clean_value(row.get("routing_risk"), "unknown"),
        "lite_gate_score": int_or_none(row.get("lite_gate_score")),
        "code_quality_score": int_or_none(row.get("code_quality_score")),
        "can_claim_true_4_8": parse_bool(row.get("can_claim_true_4_8")),
        "can_use_for_low_risk": parse_bool(row.get("can_use_for_low_risk")),
        "can_use_for_coding": parse_bool(row.get("can_use_for_coding")),
        "can_replace_reference_model": parse_bool(row.get("can_replace_reference_model")),
        "decision": clean_value(row.get("decision"), "unknown"),
        "latest_record_path": display_path(root, record_path),
        "notes": notes,
        "evidence_flags": extract_evidence_flags(combined_text),
    })


def provider_from_comparison_row(root, row, warnings):
    record_path = resolve_source_path(root, row.get("run_record_path", ""))
    record_text = read_text_if_exists(
        record_path,
        warnings,
        clean_value(row.get("provider_alias"), "unknown provider"),
    )
    return apply_methodology({
        "source_type": "provider_4_8_comparison",
        "date": clean_value(row.get("run_date")),
        "provider_id": clean_value(row.get("provider_alias"), "unknown"),
        "alias_id": clean_value(row.get("route_or_profile_alias"), clean_value(row.get("provider_alias"), "unknown")),
        "claimed_model": clean_value(row.get("claimed_model"), "unknown"),
        "expected_upstream_model": "unknown",
        "identity_status": clean_value(row.get("identity_status"), "unknown"),
        "capability_status": "unknown",
        "quality_status": clean_value(row.get("quality_status"), "unknown"),
        "code_quality_status": clean_value(row.get("code_quality_status"), "unknown"),
        "routing_risk": "unknown",
        "lite_gate_score": None,
        "code_quality_score": None,
        "can_claim_true_4_8": None,
        "can_use_for_low_risk": None,
        "can_use_for_coding": parse_bool(row.get("can_use_for_coding")),
        "can_replace_reference_model": None,
        "decision": clean_value(row.get("recommended_next_action"), "unknown"),
        "latest_record_path": display_path(root, record_path),
        "notes": clean_value(row.get("notes")),
        "evidence_flags": extract_evidence_flags(record_text),
    })


def is_lite_gate_template(path, text):
    name = path.name.lower()
    if name.startswith("_") or name.startswith("readme"):
        return True
    lowered = (text or "").lower()
    return "candidate_alias: replace_me" in lowered or "pass | hold | reject" in lowered


def provider_from_lite_gate_record(root, path, warnings):
    text = read_text_if_exists(path, warnings, path.name)
    alias = normalize_quoted_value(extract_yaml_like_value(text, "candidate_alias"))
    created_at = normalize_quoted_value(extract_yaml_like_value(text, "created_at"))
    label = normalize_quoted_value(extract_yaml_like_value(text, "label"))
    code_status = normalize_quoted_value(extract_nested_yaml_value(text, "code_quality", "status"))
    next_action = normalize_quoted_value(extract_yaml_like_value(text, "next_action"))
    raw_saved = parse_bool(extract_yaml_like_value(text, "raw_output_saved_before_judging"))
    can_use_for_coding = parse_bool(extract_yaml_like_value(text, "can_use_for_coding"))
    score_total = int_or_none(extract_nested_yaml_value(text, "scores", "total"))
    code_total = int_or_none(extract_nested_yaml_value(text, "code_quality", "total"))

    return apply_methodology({
        "source_type": "lite_gate",
        "date": created_at[:10] if created_at else "",
        "provider_id": clean_value(alias, path.stem.replace("_raw_and_judge", "")),
        "alias_id": clean_value(alias, path.stem.replace("_raw_and_judge", "")),
        "claimed_model": "not_applicable",
        "expected_upstream_model": "not_applicable",
        "identity_status": "not_applicable",
        "capability_status": "quality_screen_only",
        "quality_status": clean_value(label, "unknown"),
        "code_quality_status": clean_value(code_status, "unknown"),
        "routing_risk": "not_assessed",
        "lite_gate_score": score_total,
        "code_quality_score": code_total,
        "can_claim_true_4_8": None,
        "can_use_for_low_risk": None,
        "can_use_for_coding": can_use_for_coding,
        "can_replace_reference_model": None,
        "decision": clean_value(next_action, "unknown"),
        "latest_record_path": display_path(root, path),
        "notes": f"Lite Gate quality screen. raw_output_saved_before_judging={raw_saved}",
        "evidence_flags": extract_evidence_flags(text),
    })


def provider_from_auto_eval_run(root, path, warnings):
    text = read_text_if_exists(path, warnings, path.name)
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        warnings.append(f"Invalid auto eval run report JSON: {path}: {exc}")
        return None

    decision = clean_value(payload.get("decision"), "unknown")
    eval_mode = clean_value(payload.get("eval_mode"), "quick_screen_v1")
    explicit_eval_profile = clean_value(payload.get("eval_profile"))
    eval_profile = infer_eval_profile(payload)
    verdict_scope = infer_verdict_scope(payload, eval_profile)
    raw_capability_score = int_or_none(payload.get("capability_score"))
    raw_coding_score = int_or_none(payload.get("coding_score"))
    screen_score = int_or_none(payload.get("screen_score"))
    coding_score = raw_coding_score
    coding_axis_score = int_or_none(payload.get("coding_axis_score"))
    agent_tool_use_score = int_or_none(payload.get("agent_tool_use_score"))
    if screen_score is None and eval_profile == "screen":
        screen_score = raw_capability_score
    if coding_axis_score is None and eval_profile == "coding_only":
        coding_axis_score = raw_coding_score if raw_coding_score is not None else raw_capability_score
    elif coding_axis_score is None and eval_profile == "screen":
        coding_axis_score = raw_coding_score
    if coding_axis_score is not None:
        coding_score = coding_axis_score
    run_status = clean_value(payload.get("status"), "unknown")
    provider_error = any(
        isinstance(item, dict)
        and bool(set(item.get("evidence_flags") or []) & PROVIDER_FAILURE_CODES)
        for item in (payload.get("task_results") or [])
    )
    can_use_for_coding = payload.get("can_use_for_coding")
    if can_use_for_coding is None:
        if decision == "CONTINUE_TRIAL" and coding_score is not None and coding_score >= 80:
            can_use_for_coding = True
        elif decision == "REJECT_WEAK":
            can_use_for_coding = False

    quality_status = quality_status_from_decision(decision)

    if decision == "INCONCLUSIVE" or run_status == "failed" or provider_error:
        code_quality_status = "unknown"
    elif coding_score is None:
        code_quality_status = "unknown"
    elif coding_score >= 80:
        code_quality_status = "pass"
    elif coding_score >= 60:
        code_quality_status = "hold"
    else:
        code_quality_status = "reject"

    normalized_task_results = normalize_task_results(payload.get("task_results"))
    computed_score_groups = score_groups_from_tasks(normalized_task_results)
    score_groups = payload.get("score_groups") or computed_score_groups
    core_capability_score = int_or_none(payload.get("core_capability_score"))
    if core_capability_score is None:
        core_capability_score = score_groups.get("core_capability", {}).get("score")
    workflow_compatibility_score = int_or_none(payload.get("workflow_compatibility_score"))
    if workflow_compatibility_score is None:
        workflow_compatibility_score = score_groups.get("workflow_compatibility", {}).get("score")
    if agent_tool_use_score is None and eval_profile == "agent_tool_use":
        agent_tool_use_score = score_groups.get("agent_tool_use", {}).get("score")
    flags = extract_task_result_flags(payload.get("task_results"))
    if payload.get("hard_reject_triggered"):
        flags.append("hard_reject_triggered")

    capability_score = None if eval_profile in SCOPED_EVAL_PROFILES else raw_capability_score
    contract_mismatch = has_legacy_coding_probe_contract_mismatch(
        eval_mode,
        explicit_eval_profile,
        normalized_task_results,
    )
    score_basis = payload.get("score_basis") or {
        "run_count": 1,
        "task_count": len(normalized_task_results),
        "perturbed_checked": False,
        "holdout_checked": eval_mode == "holdout_screen_v1",
        "baseline_present": False,
    }
    decision_v2 = clean_value(
        payload.get("decision_v2"),
        default_decision_v2(
            eval_mode,
            run_status,
            bool(payload.get("hard_reject_triggered")),
            screen_score,
            coding_axis_score,
            agent_tool_use_score,
        ),
    )
    if (
        eval_profile == "screen"
        and decision == "REJECT_WEAK"
        and decision_v2 == "LIMITED_USE"
        and run_status == "completed"
        and not provider_error
        and not payload.get("hard_reject_triggered")
    ):
        decision = "LIMITED_USE"
        quality_status = quality_status_from_decision(decision)
        flags.append("legacy_decision_reinterpreted")

    if decision == "INCONCLUSIVE" or run_status == "failed" or provider_error:
        capability_score = None
        screen_score = None
        coding_score = None
        coding_axis_score = None
        can_use_for_coding = None
    elif contract_mismatch:
        flags.append("needs_rerun_after_probe_contract_fix")
        decision_v2 = "RERUN_REQUIRED"
        quality_status = "hold"
        code_quality_status = "unknown"
        can_use_for_coding = None

    route_metadata = route_metadata_from_payload(payload)
    route_key = route_metadata["route_key"]
    route_comparable = bool(route_metadata["route_comparable"])
    if not route_comparable:
        flags.append("route_key_unknown_host")
    flags = sorted(set(flags))
    routing_risk = "high" if not route_comparable else "unknown"

    return apply_methodology({
        "source_type": "auto_eval_quick_screen",
        "date": clean_value(payload.get("created_at"))[:10],
        "provider_id": clean_value(payload.get("provider_alias"), path.parent.name),
        "alias_id": clean_value(payload.get("provider_alias"), path.parent.name),
        "route_key": route_key,
        "route_fingerprint": route_metadata["route_fingerprint"],
        "base_url_host_hash": route_metadata["base_url_host_hash"],
        "protocol_resolved": route_metadata["protocol_resolved"],
        "route_comparable": route_comparable,
        "claimed_model": clean_value(payload.get("claimed_model"), "unknown"),
        "expected_upstream_model": "unknown",
        "identity_status": "route_unverified",
        "eval_profile": eval_profile,
        "verdict_scope": verdict_scope,
        "capability_status": eval_mode,
        "quality_status": quality_status,
        "code_quality_status": code_quality_status,
        "routing_risk": routing_risk,
        "lite_gate_score": None,
        "code_quality_score": None,
        "screen_score": screen_score,
        "coding_axis_score": coding_axis_score,
        "agent_tool_use_score": agent_tool_use_score,
        "core_capability_score": core_capability_score,
        "workflow_compatibility_score": workflow_compatibility_score,
        "score_groups": score_groups,
        "capability_score": capability_score,
        "capability_tier": clean_value(payload.get("capability_tier"), "TIER_UNKNOWN"),
        "coding_score": coding_score,
        "coverage_map": payload.get("coverage_map") or default_coverage_map(eval_mode),
        "score_basis": score_basis,
        "not_proven": list(payload.get("not_proven") or default_not_proven(eval_mode)),
        "decision_v2": decision_v2,
        "can_claim_true_4_8": None,
        "can_use_for_low_risk": False,
        "can_use_for_coding": can_use_for_coding,
        "can_replace_reference_model": None,
        "decision": decision,
        "latest_record_path": display_path(root, path),
        "notes": (
            f"Auto eval quick screen. "
            f"run_status={run_status}; "
            f"reported_tier={clean_value(payload.get('capability_tier'), 'unknown')}"
        ),
        "evidence_flags": flags,
        "task_results": normalized_task_results,
    })


def read_lite_gate_records(root, warnings):
    runs_root = Path(root) / "lite_gate" / "runs"
    if not runs_root.exists():
        warnings.append(f"Missing Lite Gate runs directory: {runs_root}")
        return []
    records = []
    for path in sorted(runs_root.rglob("*.md")):
        text = read_text_if_exists(path, warnings, path.name)
        if is_lite_gate_template(path, text):
            continue
        if "candidate_alias" not in text:
            continue
        records.append(provider_from_lite_gate_record(root, path, warnings))
    return records


def read_auto_eval_runs(root, warnings):
    runs_root = Path(root) / "auto_eval_runs"
    if not runs_root.exists():
        return []

    records = []
    for path in sorted(runs_root.rglob("run_report.json")):
        provider = provider_from_auto_eval_run(root, path, warnings)
        if provider:
            records.append(provider)
    return records


def read_csv_rows(path, warnings):
    if not path.exists():
        warnings.append(f"Missing CSV index: {path}")
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def build_summary(providers, baselines=None):
    baselines = baselines or []
    total = len(providers)
    high_risk = sum(1 for item in providers if item.get("routing_risk") == "high")
    coding_allowed = sum(1 for item in providers if item.get("can_use_for_coding") is True)
    coding_ok = sum(
        1
        for item in providers
        if item.get("coding_score") is not None
        and item.get("coding_score") >= 70
        and item.get("can_use_for_coding") is not False
    )
    capability_ok = sum(
        1 for item in providers if item.get("capability_tier") in CAPABILITY_TIERS_STRONG_OR_BETTER
    )
    needs_more_data = sum(1 for item in providers if item.get("recommended_use") == "needs_more_data")
    do_not_use = sum(1 for item in providers if item.get("recommended_use") == "do_not_use")
    coding_trial = sum(1 for item in providers if item.get("recommended_use") == "coding_trial")
    low_risk_use = sum(1 for item in providers if item.get("recommended_use") == "low_risk_use")
    identity_verified = sum(1 for item in providers if item.get("identity_status") == "identity_verified")
    confirmed_downgrade = sum(1 for item in providers if item.get("identity_status") == "confirmed_downgrade")
    route_unverified = sum(1 for item in providers if item.get("identity_status") in {"route_unverified", "identity_unverified", "unknown"})
    lite_gate_count = sum(1 for item in providers if item.get("source_type") == "lite_gate")
    auto_eval_count = sum(1 for item in providers if item.get("source_type") == "auto_eval_quick_screen")
    identity_record_count = sum(1 for item in providers if item.get("source_type") == "provider_identity_check")
    comparison_record_count = sum(1 for item in providers if item.get("source_type") == "provider_4_8_comparison")
    baseline_missing = sum(1 for item in baselines if item.get("status") == "baseline_missing")
    return {
        "total_records": total,
        "total_providers": total,
        "baseline_count": len(baselines),
        "baseline_missing_count": baseline_missing,
        "identity_record_count": identity_record_count,
        "comparison_record_count": comparison_record_count,
        "lite_gate_record_count": lite_gate_count,
        "auto_eval_record_count": auto_eval_count,
        "identity_verified_count": identity_verified,
        "confirmed_downgrade_count": confirmed_downgrade,
        "route_unverified_count": route_unverified,
        "high_risk_count": high_risk,
        "coding_allowed_count": coding_allowed,
        "coding_ok_count": coding_ok,
        "capability_ok_count": capability_ok,
        "needs_more_data_count": needs_more_data,
        "do_not_use_count": do_not_use,
        "coding_trial_count": coding_trial,
        "low_risk_use_count": low_risk_use,
    }


def build_report(root):
    root = Path(root)
    warnings = []
    providers = []
    baselines, baseline_warnings = load_baseline_registry(root)
    warnings.extend(baseline_warnings)
    baselines_by_id = {item["model_id"]: item for item in baselines}

    identity_index = root / "provider_identity_check" / "results_index.csv"
    for row in read_csv_rows(identity_index, warnings):
        if any(clean_value(value) for value in row.values()):
            providers.append(provider_from_identity_row(root, row, warnings))

    comparison_index = root / "provider_4_8_comparison" / "results_index.csv"
    for row in read_csv_rows(comparison_index, warnings):
        if any(clean_value(value) for value in row.values()):
            providers.append(provider_from_comparison_row(root, row, warnings))

    providers.extend(read_lite_gate_records(root, warnings))
    providers.extend(read_auto_eval_runs(root, warnings))

    providers = [attach_baseline_reference(provider, baselines_by_id) for provider in providers]
    providers.sort(key=lambda item: (item.get("date", ""), item.get("provider_id", "")), reverse=True)
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "project_root": str(root.resolve()),
        "summary": build_summary(providers, baselines),
        "providers": providers,
        "baselines": baselines,
        "warnings": warnings,
        "methodology": {
            "capability_first_evaluation": True,
            "capability_is_not_identity": True,
            "raw_outputs_preserved_in_source_files": True,
            "live_api_calls_from_site": True,
            "secret_storage_in_site": False,
        },
    }


def write_report(root, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = build_report(root)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main():
    parser = argparse.ArgumentParser(description="Build Provider Verify static site data.")
    parser.add_argument("--root", default=".", help="Project root to scan")
    parser.add_argument(
        "--output",
        default="provider_verify_site/data/report_index.json",
        help="Output JSON path",
    )
    args = parser.parse_args()

    report = write_report(args.root, args.output)
    print(f"[OK] wrote {args.output}")
    print(f"[OK] providers: {report['summary']['total_providers']}")
    if report["warnings"]:
        print(f"[WARN] warnings: {len(report['warnings'])}")
        for warning in report["warnings"]:
            print(f" - {warning}")


if __name__ == "__main__":
    main()
