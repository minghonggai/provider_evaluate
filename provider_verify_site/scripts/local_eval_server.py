import argparse
import json
import mimetypes
import re
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from provider_verify_site.scripts.auto_eval_storage import (
    build_route_metadata,
    build_route_key,
    build_run_manifest,
    ensure_run_workspace,
    persist_json_artifact,
    persist_run_manifest,
    persist_text_artifact,
    task_artifact_paths,
    unique_run_id,
)
from provider_verify_site.scripts.build_site_data import write_report
from provider_verify_site.scripts.decision_aggregator_v1 import aggregate_task_results
from provider_verify_site.scripts.provider_runner import (
    AUTO_PROTOCOL,
    ProviderRequestError,
    list_provider_models,
    resolve_provider_protocol,
    run_provider_prompt,
)
from provider_verify_site.scripts.scorers_v1 import (
    score_boundary_safety,
    score_coding_fix,
    score_data_table_analysis,
    score_evidence_honesty,
    score_instruction_following,
    score_product_communication,
    score_reasoning_planning,
    score_tool_plan_schema,
)
from provider_verify_site.scripts.task_pack_v1 import (
    get_agent_tool_use_task_pack,
    get_coding_probe_task_pack,
    get_holdout_screen_task_pack,
    get_quick_screen_task_pack,
    get_screen_v2_task_pack,
)


PROMPT_PACKS = {
    "pack_a_default": {"task_count": 5, "prompt_file": "lite_gate/lite_gate_one_prompt_zh.md"},
    "pack_b_perturbed": {"task_count": 5, "prompt_file": "lite_gate/lite_gate_one_prompt_pack_b_zh.md"},
    "pack_h_holdout": {"task_count": 3, "prompt_file": "lite_gate/lite_gate_holdout_pack_zh.md"},
}
MEMORY_STATUSES = {"off", "on", "unknown", "not_supported"}
LABELS = {"PASS", "HOLD", "REJECT"}
CODE_STATUSES = {"pass", "hold", "reject"}
MAX_JSON_BYTES = 5 * 1024 * 1024
DEFAULT_ALLOWED_ORIGINS = {
    "http://127.0.0.1:8765",
    "http://localhost:8765",
    "http://127.0.0.1:8766",
    "http://localhost:8766",
    "http://127.0.0.1:8075",
    "http://localhost:8075",
}
QUICK_SCREEN_EVAL_MODE = "quick_screen_v1"
CODING_PROBE_EVAL_MODE = "coding_probe_v1"
SCREEN_V2_EVAL_MODE = "screen_v2"
HOLDOUT_SCREEN_EVAL_MODE = "holdout_screen_v1"
AGENT_TOOL_USE_EVAL_MODE = "agent_tool_use_v1"
EVAL_MODES = {
    QUICK_SCREEN_EVAL_MODE,
    CODING_PROBE_EVAL_MODE,
    SCREEN_V2_EVAL_MODE,
    HOLDOUT_SCREEN_EVAL_MODE,
    AGENT_TOOL_USE_EVAL_MODE,
}
SCREEN_EVAL_MODES = {QUICK_SCREEN_EVAL_MODE, SCREEN_V2_EVAL_MODE, HOLDOUT_SCREEN_EVAL_MODE}
PROVIDER_PROTOCOLS = {AUTO_PROTOCOL, "openai_chat", "anthropic_messages"}

SCORER_DISPATCH = {
    "boundary_safety": score_boundary_safety,
    "instruction_following": score_instruction_following,
    "evidence_honesty": score_evidence_honesty,
    "reasoning_planning": score_reasoning_planning,
    "data_table_analysis": score_data_table_analysis,
    "coding_fix": score_coding_fix,
    "product_communication": score_product_communication,
    "tool_plan_schema": score_tool_plan_schema,
}


class LocalEvalError(Exception):
    def __init__(self, message, status=400, error_code="VALIDATION_ERROR"):
        super().__init__(message)
        self.status = status
        self.error_code = error_code


def safe_slug(value):
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip().lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "candidate"


def _clean(value, default=""):
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


def _bool_value(value, field):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    raise LocalEvalError(f"{field} must be true or false")


def _int_range(value, field, minimum=0, maximum=100):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise LocalEvalError(f"{field} must be an integer") from None
    if number < minimum or number > maximum:
        raise LocalEvalError(f"{field} must be between {minimum} and {maximum}")
    return number


def validate_lite_gate_payload(payload):
    if not isinstance(payload, dict):
        raise LocalEvalError("payload must be a JSON object")

    alias = _clean(payload.get("candidate_alias"))
    raw_output = _clean(payload.get("raw_candidate_output"))
    if not alias:
        raise LocalEvalError("candidate_alias is required")
    if not raw_output:
        raise LocalEvalError("raw_candidate_output is required")

    prompt_pack = _clean(payload.get("prompt_pack"), "pack_a_default")
    if prompt_pack not in PROMPT_PACKS:
        raise LocalEvalError(f"prompt_pack must be one of: {', '.join(PROMPT_PACKS)}")

    memory_status = _clean(payload.get("memory_status"), "unknown")
    if memory_status not in MEMORY_STATUSES:
        raise LocalEvalError(f"memory_status must be one of: {', '.join(sorted(MEMORY_STATUSES))}")

    label = _clean(payload.get("label"), "HOLD").upper()
    if label not in LABELS:
        raise LocalEvalError("label must be PASS, HOLD, or REJECT")

    scores_input = payload.get("scores") or {}
    if not isinstance(scores_input, dict):
        raise LocalEvalError("scores must be an object")
    score_fields = [
        "task_1_project_handoff",
        "task_2_boundary_safety",
        "task_3_evidence_execution",
        "task_4_coding",
        "task_5_chinese_product_communication",
        "total",
    ]
    scores = {
        field: _int_range(scores_input.get(field, 0), f"scores.{field}", 0, 100)
        for field in score_fields
    }

    code_input = payload.get("code_quality") or {}
    if not isinstance(code_input, dict):
        raise LocalEvalError("code_quality must be an object")
    code_status = _clean(code_input.get("status"), "hold").lower()
    if code_status not in CODE_STATUSES:
        raise LocalEvalError("code_quality.status must be pass, hold, or reject")
    code_quality = {
        "correctness": _int_range(code_input.get("correctness", 0), "code_quality.correctness", 0, 5),
        "change_precision": _int_range(code_input.get("change_precision", 0), "code_quality.change_precision", 0, 5),
        "testing_awareness": _int_range(code_input.get("testing_awareness", 0), "code_quality.testing_awareness", 0, 5),
        "maintainability": _int_range(code_input.get("maintainability", 0), "code_quality.maintainability", 0, 5),
        "total": _int_range(code_input.get("total", 0), "code_quality.total", 0, 20),
        "status": code_status,
    }

    return {
        "candidate_alias": alias,
        "claimed_model": _clean(payload.get("claimed_model"), "unknown"),
        "route_or_profile_alias": _clean(payload.get("route_or_profile_alias")),
        "prompt_pack": prompt_pack,
        "memory_status": memory_status,
        "raw_candidate_output": raw_output,
        "scores": scores,
        "code_quality": code_quality,
        "label": label,
        "can_use_for_coding": _bool_value(payload.get("can_use_for_coding", False), "can_use_for_coding"),
        "next_action": _clean(payload.get("next_action"), "needs_more_data"),
        "reviewer_notes": _clean(payload.get("reviewer_notes"), "No reviewer notes recorded."),
        "force": _bool_value(payload.get("force", False), "force"),
    }


def validate_quick_screen_payload(payload):
    if not isinstance(payload, dict):
        raise LocalEvalError("payload must be a JSON object")

    provider_alias = _clean(payload.get("provider_alias"))
    base_url = _clean(payload.get("base_url"))
    api_key = _clean(payload.get("api_key"))
    model_name = _clean(payload.get("model_name"))
    provider_protocol = _clean(payload.get("provider_protocol"), AUTO_PROTOCOL).lower()
    claimed_model = _clean(payload.get("claimed_model")) or model_name or "unknown"
    eval_mode = _clean(payload.get("eval_mode"), QUICK_SCREEN_EVAL_MODE)

    if not provider_alias:
        raise LocalEvalError("provider_alias is required")
    if not base_url:
        raise LocalEvalError("base_url is required")
    if not api_key:
        raise LocalEvalError("api_key is required")
    if not model_name:
        raise LocalEvalError("model_name is required")
    if provider_protocol not in PROVIDER_PROTOCOLS:
        raise LocalEvalError(
            f"provider_protocol must be one of: {', '.join(sorted(PROVIDER_PROTOCOLS))}"
        )
    if eval_mode not in EVAL_MODES:
        raise LocalEvalError(f"eval_mode must be one of: {', '.join(sorted(EVAL_MODES))}")

    return {
        "provider_alias": provider_alias,
        "claimed_model": claimed_model,
        "base_url": base_url,
        "api_key": api_key,
        "model_name": model_name,
        "provider_protocol": provider_protocol,
        "eval_mode": eval_mode,
    }


def validate_provider_models_payload(payload):
    if not isinstance(payload, dict):
        raise LocalEvalError("payload must be a JSON object")

    base_url = _clean(payload.get("base_url"))
    api_key = _clean(payload.get("api_key"))
    provider_protocol = _clean(payload.get("provider_protocol"), AUTO_PROTOCOL).lower()

    if not base_url:
        raise LocalEvalError("base_url is required")
    if not api_key:
        raise LocalEvalError("api_key is required")
    if provider_protocol not in PROVIDER_PROTOCOLS:
        raise LocalEvalError(
            f"provider_protocol must be one of: {', '.join(sorted(PROVIDER_PROTOCOLS))}"
        )

    return {
        "base_url": base_url,
        "api_key": api_key,
        "provider_protocol": provider_protocol,
    }


def list_provider_models_for_payload(payload, model_lister=None):
    normalized = validate_provider_models_payload(payload)
    model_lister = model_lister or list_provider_models
    result = model_lister(
        base_url=normalized["base_url"],
        api_key=normalized["api_key"],
        provider_protocol=normalized["provider_protocol"],
    )
    models = result.get("models") or []
    return {
        "status": result.get("status", "ok"),
        "protocol": result.get("protocol", normalized["provider_protocol"]),
        "models": models,
        "model_count": result.get("model_count", len(models)),
    }


def _yaml_quote(value):
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_lite_gate_record(payload, now=None):
    now = now or datetime.now().astimezone()
    pack = PROMPT_PACKS[payload["prompt_pack"]]
    can_code = "true" if payload["can_use_for_coding"] else "false"
    force_note = "Generated by local_eval_server.py. Raw output is preserved before judgement."

    return f"""# Lite Gate Run Record

## Metadata

```yaml
eval_type: lite_gate_one_prompt_v2
prompt_pack: {payload["prompt_pack"]}
task_count: {pack["task_count"]}
candidate_alias: {_yaml_quote(payload["candidate_alias"])}
claimed_model: {_yaml_quote(payload["claimed_model"])}
route_or_profile_alias: {_yaml_quote(payload["route_or_profile_alias"])}
reference_model: opus 4.8 ai
fresh_context: unknown
memory_status: {payload["memory_status"]}
raw_output_saved_before_judging: true
saw_other_candidate_output: false
saw_prior_judge_notes: false
close_call_escalation: false
optional_code_probe_run: false
judge: human
created_at: {now.astimezone().isoformat(timespec="seconds")}
source_prompt_file: {pack["prompt_file"]}
```

## Raw Candidate Output

```text
{payload["raw_candidate_output"]}
```

## Quick Judge

```yaml
hard_reject_triggered: false
hard_reject_reason: ""

scores:
  task_1_project_handoff: {payload["scores"]["task_1_project_handoff"]}
  task_2_boundary_safety: {payload["scores"]["task_2_boundary_safety"]}
  task_3_evidence_execution: {payload["scores"]["task_3_evidence_execution"]}
  task_4_coding: {payload["scores"]["task_4_coding"]}
  task_5_chinese_product_communication: {payload["scores"]["task_5_chinese_product_communication"]}
  total: {payload["scores"]["total"]}

code_quality:
  correctness: {payload["code_quality"]["correctness"]}
  change_precision: {payload["code_quality"]["change_precision"]}
  testing_awareness: {payload["code_quality"]["testing_awareness"]}
  maintainability: {payload["code_quality"]["maintainability"]}
  total: {payload["code_quality"]["total"]}
  status: {payload["code_quality"]["status"]}

label: {payload["label"]}

main_gap_vs_reference:
  - ""

allowed_use:
  - ""

can_use_for_coding: {can_code}

next_action: {_yaml_quote(payload["next_action"])}
```

## Reviewer Notes

```text
{payload["reviewer_notes"]}

{force_note}
```
"""


def create_lite_gate_run(root, payload, now=None):
    root = Path(root)
    normalized = validate_lite_gate_payload(payload)
    now = now or datetime.now().astimezone()
    date_dir = now.astimezone().strftime("%Y-%m-%d")
    filename = f"{safe_slug(normalized['candidate_alias'])}_raw_and_judge.md"
    run_dir = root / "lite_gate" / "runs" / date_dir
    record_path = run_dir / filename

    if record_path.exists() and not normalized["force"]:
        raise LocalEvalError(f"run record already exists: {record_path}", status=409)

    run_dir.mkdir(parents=True, exist_ok=True)
    record_path.write_text(build_lite_gate_record(normalized, now), encoding="utf-8")

    report_path = root / "provider_verify_site" / "data" / "report_index.json"
    write_report(root, report_path)

    return {
        "status": "created",
        "record_path": record_path.relative_to(root).as_posix(),
        "report_path": report_path.relative_to(root).as_posix(),
        "prompt_file": PROMPT_PACKS[normalized["prompt_pack"]]["prompt_file"],
    }


def _quick_screen_run_dir(root, run_id):
    root = Path(root)
    runs_root = root / "auto_eval_runs"
    if not runs_root.exists():
        return None
    matches = list(runs_root.glob(f"*/{run_id}"))
    return matches[0] if matches else None


def _ensure_unique_run_workspace(root, run_id, now):
    root = Path(root)
    date_dir = now.astimezone().strftime("%Y-%m-%d")
    runs_root = root / "auto_eval_runs" / date_dir
    candidate = run_id
    counter = 2
    while (runs_root / candidate).exists():
        candidate = f"{run_id}-{counter}"
        counter += 1
    return candidate, ensure_run_workspace(root, candidate, now)


def _score_task(task, response_text):
    scorer = SCORER_DISPATCH.get(task["scorer"])
    if scorer is None:
        raise LocalEvalError(f"unknown scorer: {task['scorer']}", error_code="SCORER_ERROR")
    if task["scorer"] == "coding_fix":
        return scorer(response_text, verifier_code=task["verifier_code"])
    return scorer(response_text)


def _error_task_result(task, error_code, message):
    return {
        "task_id": task["task_id"],
        "score": 0,
        "max_score": task["max_score"],
        "task_status": "error",
        "hard_reject": False,
        "evidence_flags": [error_code],
        "notes": [message],
    }


def _base_coverage_map():
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


def _coverage_map_for_eval_mode(eval_mode):
    coverage = _base_coverage_map()
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
        if eval_mode in {SCREEN_V2_EVAL_MODE, HOLDOUT_SCREEN_EVAL_MODE}:
            coverage["reasoning_planning"] = "shallow"
            coverage["data_analysis"] = "shallow"
    elif eval_mode == CODING_PROBE_EVAL_MODE:
        coverage["coding"] = "standard"
    elif eval_mode == AGENT_TOOL_USE_EVAL_MODE:
        coverage["agent_tool_use"] = "shallow"
    return coverage


def _not_proven_for_eval_mode(eval_mode):
    if eval_mode in SCREEN_EVAL_MODES:
        return [
            "full general capability",
            "provider identity",
            "long-context capability",
            "multi-session route stability",
            "production suitability",
        ]
    if eval_mode == CODING_PROBE_EVAL_MODE:
        return [
            "full general capability",
            "provider identity",
            "long-context capability",
            "multi-session route stability",
            "non-coding capability axes",
        ]
    if eval_mode == AGENT_TOOL_USE_EVAL_MODE:
        return [
            "full general capability",
            "actual tool execution reliability",
            "provider identity",
            "live-system safety",
            "long-context capability",
            "multi-session route stability",
        ]
    return ["provider identity", "long-context capability", "multi-session route stability"]


def _decision_v2(eval_mode, summary):
    if summary["run_status"] != "completed":
        return "INCONCLUSIVE"
    if summary["hard_reject_triggered"]:
        return "REJECTED"
    if eval_mode in SCREEN_EVAL_MODES:
        score = int(summary.get("capability_score") or 0)
        if score >= 80 and int(summary.get("coding_score") or 0) >= 60:
            return "TRIAL_RECOMMENDED"
        if score >= 65:
            return "LIMITED_USE"
        return "NOT_RECOMMENDED"
    if eval_mode == CODING_PROBE_EVAL_MODE:
        score = int(summary.get("coding_score") or 0)
        if score >= 85:
            return "TRIAL_RECOMMENDED"
        if score >= 70:
            return "LIMITED_USE"
        return "NOT_RECOMMENDED"
    if eval_mode == AGENT_TOOL_USE_EVAL_MODE:
        score = int(summary.get("capability_score") or 0)
        if score >= 85:
            return "TRIAL_RECOMMENDED"
        if score >= 70:
            return "LIMITED_USE"
        return "NOT_RECOMMENDED"
    return "NEEDS_MORE_DATA"


def _profile_scope_fields(eval_mode, summary, task_count):
    score_basis = {
        "run_count": 1,
        "task_count": task_count,
        "perturbed_checked": False,
        "holdout_checked": eval_mode == HOLDOUT_SCREEN_EVAL_MODE,
        "baseline_present": False,
    }
    common = {
        "coverage_map": _coverage_map_for_eval_mode(eval_mode),
        "score_basis": score_basis,
        "not_proven": _not_proven_for_eval_mode(eval_mode),
        "decision_v2": _decision_v2(eval_mode, summary),
        "core_capability_score": summary.get("core_capability_score"),
        "workflow_compatibility_score": summary.get("workflow_compatibility_score"),
        "score_groups": summary.get("score_groups", {}),
    }

    if eval_mode in SCREEN_EVAL_MODES:
        return {
            **common,
            "eval_profile": "screen",
            "verdict_scope": (
                "holdout_screen_triage"
                if eval_mode == HOLDOUT_SCREEN_EVAL_MODE
                else "screen_triage"
            ),
            "screen_score": summary["capability_score"],
            "coding_axis_score": summary["coding_score"],
            "capability_score": None,
            "capability_tier": "TIER_UNKNOWN",
        }

    if eval_mode == CODING_PROBE_EVAL_MODE:
        return {
            **common,
            "eval_profile": "coding_only",
            "verdict_scope": "coding_only",
            "screen_score": None,
            "coding_axis_score": summary["coding_score"],
            "capability_score": None,
            "capability_tier": "TIER_UNKNOWN",
        }

    if eval_mode == AGENT_TOOL_USE_EVAL_MODE:
        return {
            **common,
            "eval_profile": "agent_tool_use",
            "verdict_scope": "tool_use_schema_triage",
            "agent_tool_use_score": summary["capability_score"],
            "screen_score": None,
            "coding_axis_score": None,
            "capability_score": None,
            "capability_tier": "TIER_UNKNOWN",
        }

    return {
        **common,
        "eval_profile": "unknown",
        "verdict_scope": "unknown",
        "screen_score": None,
        "coding_axis_score": None,
        "capability_score": summary["capability_score"],
        "capability_tier": summary["capability_tier"],
    }


def _request_record(run_id, task, request_id, model_name, provider_protocol, sent_at):
    return {
        "run_id": run_id,
        "task_id": task["task_id"],
        "request_id": request_id,
        "sent_at": sent_at.isoformat(timespec="seconds"),
        "base_url": "masked",
        "model_name": model_name,
        "provider_protocol": provider_protocol,
        "temperature": task["temperature"],
        "max_tokens": task["max_tokens"],
        "messages": [
            {
                "role": "user",
                "content": task["prompt"],
            }
        ],
    }


def _response_record(run_id, task_id, request_id, response, received_at):
    return {
        "run_id": run_id,
        "task_id": task_id,
        "request_id": request_id,
        "received_at": received_at.isoformat(timespec="seconds"),
        "latency_ms": response.get("latency_ms", 0),
        "status": response.get("status", "ok"),
        "provider_protocol": response.get("protocol", "unknown"),
        "finish_reason": response.get("finish_reason", "unknown"),
        "raw_text_path": f"{task_id}_response.txt",
        "raw_json_path": f"{task_id}_response.json",
        "response_contract_summary": response.get("response_contract_summary", {}),
    }


def _run_quick_screen_tasks(root, run_dir, run_id, normalized, now=None, runner=None):
    runner = runner or run_provider_prompt
    now = now or datetime.now().astimezone()
    if normalized["eval_mode"] == CODING_PROBE_EVAL_MODE:
        task_pack = get_coding_probe_task_pack()
    elif normalized["eval_mode"] == SCREEN_V2_EVAL_MODE:
        task_pack = get_screen_v2_task_pack()
    elif normalized["eval_mode"] == HOLDOUT_SCREEN_EVAL_MODE:
        task_pack = get_holdout_screen_task_pack()
    elif normalized["eval_mode"] == AGENT_TOOL_USE_EVAL_MODE:
        task_pack = get_agent_tool_use_task_pack()
    else:
        task_pack = get_quick_screen_task_pack()
    protocol_resolved = resolve_provider_protocol(
        normalized["provider_protocol"],
        normalized["base_url"],
        normalized["model_name"],
    )
    route_metadata = build_route_metadata(
        normalized["provider_protocol"],
        normalized["base_url"],
        normalized["model_name"],
        protocol_resolved=protocol_resolved,
    )
    manifest = build_run_manifest(
        run_id=run_id,
        provider_alias=normalized["provider_alias"],
        claimed_model=normalized["claimed_model"],
        model_name=normalized["model_name"],
        base_url=normalized["base_url"],
        provider_protocol=normalized["provider_protocol"],
        eval_mode=normalized["eval_mode"],
        task_ids=[task["task_id"] for task in task_pack],
        now=now,
    )
    manifest.update(route_metadata)
    manifest["status"] = "created"
    manifest["completed_tasks"] = 0
    manifest["total_tasks"] = len(task_pack)
    persist_run_manifest(run_dir, manifest)

    manifest["status"] = "running"
    persist_run_manifest(run_dir, manifest)

    task_results = []
    for index, task in enumerate(task_pack, start=1):
        request_id = f"{task['task_id']}-{index:03d}"
        artifact_paths = task_artifact_paths(run_dir, task["task_id"])
        sent_at = datetime.now().astimezone()
        request_record = _request_record(
            run_id,
            task,
            request_id,
            normalized["model_name"],
            normalized["provider_protocol"],
            sent_at,
        )
        persist_json_artifact(artifact_paths["request_json"], request_record)

        try:
            response = runner(
                base_url=normalized["base_url"],
                api_key=normalized["api_key"],
                model_name=normalized["model_name"],
                provider_protocol=normalized["provider_protocol"],
                prompt_text=task["prompt"],
                temperature=task["temperature"],
                max_tokens=task["max_tokens"],
            )
            received_at = datetime.now().astimezone()
            persist_text_artifact(artifact_paths["response_text"], response.get("response_text", ""))
            persist_json_artifact(
                artifact_paths["response_json"],
                {
                    **_response_record(run_id, task["task_id"], request_id, response, received_at),
                    "raw_json": response.get("raw_json", {}),
                    "usage": response.get("usage", {}),
                },
            )
            task_result = {"task_id": task["task_id"], **_score_task(task, response.get("response_text", ""))}
        except ProviderRequestError as exc:
            persist_text_artifact(artifact_paths["response_text"], "")
            persist_json_artifact(
                artifact_paths["response_json"],
                {
                    "run_id": run_id,
                    "task_id": task["task_id"],
                    "request_id": request_id,
                    "status": "error",
                    "error_code": exc.error_code,
                    "message": str(exc),
                    "raw_json": {},
                },
            )
            task_result = _error_task_result(task, exc.error_code, str(exc))
        except Exception as exc:  # Defensive API boundary.
            persist_text_artifact(artifact_paths["response_text"], "")
            persist_json_artifact(
                artifact_paths["response_json"],
                {
                    "run_id": run_id,
                    "task_id": task["task_id"],
                    "request_id": request_id,
                    "status": "error",
                    "error_code": "TASK_RUNNER_ERROR",
                    "message": str(exc),
                    "raw_json": {},
                },
            )
            task_result = _error_task_result(task, "TASK_RUNNER_ERROR", str(exc))

        persist_json_artifact(artifact_paths["score_json"], task_result)
        task_results.append(task_result)
        manifest["completed_tasks"] = len(task_results)
        manifest["last_task_id"] = task["task_id"]
        manifest["last_request_id"] = request_id
        persist_run_manifest(run_dir, manifest)

        if task_result["task_status"] == "error":
            break

    summary = aggregate_task_results(task_results)
    scoped_scores = _profile_scope_fields(normalized["eval_mode"], summary, len(task_pack))
    finished_at = datetime.now().astimezone()
    run_report = {
        "run_id": run_id,
        "provider_alias": normalized["provider_alias"],
        "claimed_model": normalized["claimed_model"],
        "model_name": normalized["model_name"],
        **route_metadata,
        "provider_protocol": normalized["provider_protocol"],
        "eval_mode": normalized["eval_mode"],
        "status": summary["run_status"],
        "created_at": manifest["created_at"],
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "completed_tasks": len(task_results),
        "total_tasks": len(task_pack),
        "decision": summary["decision"],
        "decision_reasons": summary["decision_reasons"],
        **scoped_scores,
        "coding_score": summary["coding_score"],
        "hard_reject_triggered": summary["hard_reject_triggered"],
        "can_use_for_coding": summary["decision"] == "CONTINUE_TRIAL",
        "raw_output_saved_before_judging": True,
        "task_results": task_results,
    }
    run_report_path = run_dir / "run_report.json"
    persist_json_artifact(run_report_path, run_report)

    manifest["status"] = summary["run_status"]
    manifest["decision"] = summary["decision"]
    manifest["decision_reasons"] = summary["decision_reasons"]
    manifest.update(scoped_scores)
    manifest["coding_score"] = summary["coding_score"]
    manifest["hard_reject_triggered"] = summary["hard_reject_triggered"]
    manifest["finished_at"] = finished_at.isoformat(timespec="seconds")
    manifest["report_path"] = run_report_path.relative_to(root).as_posix()
    persist_run_manifest(run_dir, manifest)

    return run_report


def create_quick_screen_run(root, payload, now=None, runner=None):
    root = Path(root)
    normalized = validate_quick_screen_payload(payload)
    now = now or datetime.now().astimezone()
    run_id = unique_run_id(normalized["provider_alias"], now)
    run_id, run_dir = _ensure_unique_run_workspace(root, run_id, now)

    run_report = _run_quick_screen_tasks(
        root=root,
        run_dir=run_dir,
        run_id=run_id,
        normalized=normalized,
        now=now,
        runner=runner,
    )

    report_index_path = root / "provider_verify_site" / "data" / "report_index.json"
    write_report(root, report_index_path)

    return {
        "run_id": run_id,
        "status": run_report["status"],
        "decision": run_report["decision"],
        "report_path": f"auto_eval_runs/{now.astimezone().strftime('%Y-%m-%d')}/{run_id}/run_report.json",
        "poll_url": f"/api/quick-screen-runs/{run_id}",
    }


def read_quick_screen_run(root, run_id):
    root = Path(root)
    run_dir = _quick_screen_run_dir(root, run_id)
    if run_dir is None:
        raise LocalEvalError(f"run not found: {run_id}", status=404, error_code="RUN_NOT_FOUND")

    run_report_path = run_dir / "run_report.json"
    manifest_path = run_dir / "run_manifest.json"
    if run_report_path.exists():
        report = json.loads(run_report_path.read_text(encoding="utf-8"))
        return {
            "run_id": report["run_id"],
            "run_status": report["status"],
            "provider_alias": report.get("provider_alias"),
            "claimed_model": report.get("claimed_model"),
            "model_name": report.get("model_name"),
            "completed_tasks": report.get("completed_tasks", 0),
            "total_tasks": report.get("total_tasks", 0),
            "decision": report.get("decision"),
            "decision_reasons": report.get("decision_reasons", []),
            "capability_score": report.get("capability_score"),
            "screen_score": report.get("screen_score"),
            "coding_score": report.get("coding_score"),
            "coding_axis_score": report.get("coding_axis_score"),
            "core_capability_score": report.get("core_capability_score"),
            "workflow_compatibility_score": report.get("workflow_compatibility_score"),
            "capability_tier": report.get("capability_tier"),
            "decision_v2": report.get("decision_v2"),
            "eval_mode": report.get("eval_mode"),
            "eval_profile": report.get("eval_profile"),
            "verdict_scope": report.get("verdict_scope"),
            "hard_reject_triggered": report.get("hard_reject_triggered", False),
            "score_basis": report.get("score_basis", {}),
            "score_groups": report.get("score_groups", {}),
            "task_results": report.get("task_results", []),
            "report_path": run_report_path.relative_to(root).as_posix(),
        }
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return {
            "run_id": run_id,
            "run_status": manifest.get("status", "created"),
            "completed_tasks": manifest.get("completed_tasks", 0),
            "total_tasks": manifest.get("total_tasks", 0),
            "decision": manifest.get("decision"),
            "decision_v2": manifest.get("decision_v2"),
            "eval_mode": manifest.get("eval_mode"),
            "eval_profile": manifest.get("eval_profile"),
            "verdict_scope": manifest.get("verdict_scope"),
            "report_path": manifest.get("report_path"),
        }
    raise LocalEvalError(f"run not found: {run_id}", status=404, error_code="RUN_NOT_FOUND")


def list_quick_screen_runs(root, limit=20):
    root = Path(root)
    runs_root = root / "auto_eval_runs"
    if not runs_root.exists():
        return {"runs": []}

    runs = []
    for report_path in runs_root.rglob("run_report.json"):
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        runs.append(
            {
                "run_id": report.get("run_id"),
                "provider_alias": report.get("provider_alias"),
                "decision": report.get("decision"),
                "capability_score": report.get("capability_score"),
                "screen_score": report.get("screen_score"),
                "coding_score": report.get("coding_score"),
                "coding_axis_score": report.get("coding_axis_score"),
                "created_at": report.get("created_at"),
                "report_path": report_path.relative_to(root).as_posix(),
            }
        )
    runs.sort(key=lambda item: (item.get("created_at") or "", item.get("run_id") or ""), reverse=True)
    return {"runs": runs[:limit]}


class LocalEvalHandler(BaseHTTPRequestHandler):
    server_version = "LocalEvalServer/1.0"

    def _origin_allowed(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return origin in self.server.allowed_origins

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        origin = self.headers.get("Origin")
        if origin and origin in self.server.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def _send_static_file(self, parsed):
        path = unquote(parsed.path)
        if path == "/":
            path = "/index.html"
        relative_path = path.lstrip("/")
        if not relative_path or ".." in Path(relative_path).parts:
            self._send_json(404, {"error": "not found"})
            return True

        site_root = self.server.site_root.resolve()
        file_path = (site_root / relative_path).resolve()
        try:
            file_path.relative_to(site_root)
        except ValueError:
            self._send_json(404, {"error": "not found"})
            return True

        if not file_path.is_file():
            return False

        body = file_path.read_bytes()
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def do_OPTIONS(self):
        if not self._origin_allowed():
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(204)
        origin = self.headers.get("Origin")
        if origin and origin in self.server.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(
                200,
                {
                    "status": "ok",
                    "root": str(self.server.project_root),
                    "service": "local_eval_server",
                },
            )
            return

        if parsed.path == "/api/quick-screen-runs":
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["20"])[0])
            except ValueError:
                self._send_json(400, {"error": "limit must be an integer", "error_code": "VALIDATION_ERROR"})
                return
            self._send_json(200, list_quick_screen_runs(self.server.project_root, limit=limit))
            return

        if parsed.path.startswith("/api/quick-screen-runs/"):
            run_id = parsed.path.rsplit("/", 1)[-1]
            try:
                result = read_quick_screen_run(self.server.project_root, run_id)
            except LocalEvalError as exc:
                self._send_json(exc.status, {"error": str(exc), "error_code": exc.error_code})
                return
            self._send_json(200, result)
            return

        if self._send_static_file(parsed):
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if not self._origin_allowed():
            self._send_json(403, {"error": "origin not allowed", "error_code": "VALIDATION_ERROR"})
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/provider-models":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json(400, {"error": "invalid content length", "error_code": "VALIDATION_ERROR"})
                return
            if content_length <= 0 or content_length > MAX_JSON_BYTES:
                self._send_json(413, {"error": "request body too large or empty", "error_code": "VALIDATION_ERROR"})
                return

            try:
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                result = list_provider_models_for_payload(payload)
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid JSON", "error_code": "VALIDATION_ERROR"})
                return
            except LocalEvalError as exc:
                self._send_json(exc.status, {"error": str(exc), "error_code": exc.error_code})
                return
            except ProviderRequestError as exc:
                self._send_json(502, {"error": str(exc), "error_code": exc.error_code})
                return
            except Exception as exc:  # Defensive API boundary.
                self._send_json(500, {"error": str(exc), "error_code": "SERVER_ERROR"})
                return

            self._send_json(200, result)
            return

        if parsed.path == "/api/lite-gate-runs":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json(400, {"error": "invalid content length", "error_code": "VALIDATION_ERROR"})
                return
            if content_length <= 0 or content_length > MAX_JSON_BYTES:
                self._send_json(413, {"error": "request body too large or empty", "error_code": "VALIDATION_ERROR"})
                return

            try:
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                result = create_lite_gate_run(self.server.project_root, payload)
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid JSON", "error_code": "VALIDATION_ERROR"})
                return
            except LocalEvalError as exc:
                self._send_json(exc.status, {"error": str(exc), "error_code": exc.error_code})
                return
            except Exception as exc:  # Defensive API boundary.
                self._send_json(500, {"error": str(exc), "error_code": "SERVER_ERROR"})
                return

            self._send_json(201, result)
            return

        if parsed.path == "/api/quick-screen-runs":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json(400, {"error": "invalid content length", "error_code": "VALIDATION_ERROR"})
                return
            if content_length <= 0 or content_length > MAX_JSON_BYTES:
                self._send_json(413, {"error": "request body too large or empty", "error_code": "VALIDATION_ERROR"})
                return

            try:
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                result = create_quick_screen_run(self.server.project_root, payload)
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid JSON", "error_code": "VALIDATION_ERROR"})
                return
            except LocalEvalError as exc:
                self._send_json(exc.status, {"error": str(exc), "error_code": exc.error_code})
                return
            except Exception as exc:  # Defensive API boundary.
                self._send_json(500, {"error": str(exc), "error_code": "SERVER_ERROR"})
                return

            self._send_json(201, result)
            return

        self._send_json(404, {"error": "not found", "error_code": "VALIDATION_ERROR"})

    def log_message(self, format, *args):
        print(f"[local-eval] {self.address_string()} - {format % args}")


class LocalEvalHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler_class, project_root, allowed_origins):
        super().__init__(server_address, handler_class)
        self.project_root = Path(project_root).resolve()
        self.site_root = self.project_root / "provider_verify_site"
        self.allowed_origins = set(allowed_origins)
        host, port = self.server_address[:2]
        if host in {"127.0.0.1", "localhost", ""}:
            self.allowed_origins.update({f"http://127.0.0.1:{port}", f"http://localhost:{port}"})
        else:
            self.allowed_origins.add(f"http://{host}:{port}")


def run_server(root, host="127.0.0.1", port=8766, allowed_origins=None):
    allowed_origins = allowed_origins or DEFAULT_ALLOWED_ORIGINS
    server = LocalEvalHTTPServer((host, port), LocalEvalHandler, root, allowed_origins)
    print(f"[OK] local eval server listening on http://{host}:{port}")
    print(f"[OK] project root: {server.project_root}")
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Run the local model evaluation write service.")
    parser.add_argument("--root", default=".", help="model_evaluate project root")
    parser.add_argument("--host", default="127.0.0.1", help="bind host; keep 127.0.0.1 for local-only use")
    parser.add_argument("--port", type=int, default=8766, help="bind port")
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        help="allowed browser origin; can be repeated",
    )
    args = parser.parse_args()
    allowed = set(args.allow_origin) or DEFAULT_ALLOWED_ORIGINS
    run_server(args.root, args.host, args.port, allowed)


if __name__ == "__main__":
    main()
