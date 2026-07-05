import json
import re
import hashlib
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_TASK_IDS = [
    "boundary_safety",
    "instruction_following",
    "evidence_honesty",
    "coding_fix",
    "product_communication",
]


def safe_slug(value):
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip().lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "candidate"


def unique_run_id(provider_alias, now=None):
    now = now or datetime.now().astimezone()
    timestamp = now.astimezone().strftime("%Y-%m-%d-%H%M%S")
    return f"{timestamp}-{safe_slug(provider_alias)}"


def build_route_key(provider_protocol, base_url, model_name):
    host = _route_host(base_url)
    protocol = str(provider_protocol or "auto").strip().lower() or "auto"
    model = str(model_name or "unknown-model").strip().lower() or "unknown-model"
    return f"{protocol}|{host}|{model}"


def _short_hash(value):
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


def _route_host(base_url):
    raw = str(base_url or "").strip()
    if not raw:
        return "unknown-host"
    parsed = urlparse(raw)
    host = parsed.netloc
    if not host and parsed.path:
        host = parsed.path.split("/", 1)[0]
    host = host.strip().lower()
    return host or "unknown-host"


def _route_path(base_url):
    raw = str(base_url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.netloc:
        return parsed.path.rstrip("/")
    parts = parsed.path.split("/", 1)
    return f"/{parts[1].rstrip('/')}" if len(parts) > 1 else ""


def build_route_metadata(provider_protocol, base_url, model_name, protocol_resolved=None):
    protocol = str(provider_protocol or "auto").strip().lower() or "auto"
    resolved = str(protocol_resolved or protocol).strip().lower() or protocol
    model = str(model_name or "unknown-model").strip().lower() or "unknown-model"
    host = _route_host(base_url)
    path = _route_path(base_url)
    route_key = f"{protocol}|{host}|{model}"
    route_comparable = host != "unknown-host"
    fingerprint_basis = f"{protocol}|{resolved}|{host}|{path}|{model}"
    prefix = "rk1" if route_comparable else "uncomparable"
    host_hash = _short_hash(host) if route_comparable else "unknown"
    return {
        "route_key": route_key,
        "route_fingerprint": f"{prefix}:{_short_hash(fingerprint_basis)}",
        "base_url_host_hash": host_hash,
        "protocol_resolved": resolved,
        "route_comparable": route_comparable,
    }


def ensure_run_workspace(root, run_id, now=None):
    now = now or datetime.now().astimezone()
    root = Path(root)
    run_dir = root / "auto_eval_runs" / now.astimezone().strftime("%Y-%m-%d") / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def build_run_manifest(
    run_id,
    provider_alias,
    claimed_model,
    model_name,
    base_url,
    provider_protocol="auto",
    eval_mode="quick_screen_v1",
    task_ids=None,
    now=None,
):
    now = now or datetime.now().astimezone()
    return {
        "run_id": run_id,
        "provider_alias": provider_alias,
        "claimed_model": claimed_model,
        "model_name": model_name,
        **build_route_metadata(provider_protocol, base_url, model_name),
        "provider_protocol": provider_protocol,
        "eval_mode": eval_mode,
        "created_at": now.astimezone().isoformat(timespec="seconds"),
        "status": "created",
        "task_ids": list(task_ids or DEFAULT_TASK_IDS),
    }


def persist_run_manifest(run_dir, manifest):
    path = Path(run_dir) / "run_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def task_artifact_paths(run_dir, task_id):
    run_dir = Path(run_dir)
    return {
        "request_json": run_dir / f"{task_id}_request.json",
        "response_json": run_dir / f"{task_id}_response.json",
        "response_text": run_dir / f"{task_id}_response.txt",
        "score_json": run_dir / f"{task_id}_score.json",
    }


def persist_json_artifact(path, payload):
    path = Path(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def persist_text_artifact(path, text):
    path = Path(path)
    path.write_text(str(text), encoding="utf-8")
    return path
