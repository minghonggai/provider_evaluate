import json
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen


OPENAI_CHAT_PROTOCOL = "openai_chat"
ANTHROPIC_MESSAGES_PROTOCOL = "anthropic_messages"
AUTO_PROTOCOL = "auto"


class ProviderRequestError(Exception):
    def __init__(self, message, error_code="PROVIDER_REQUEST_FAILED"):
        super().__init__(message)
        self.error_code = error_code


def _normalized_base_url(base_url):
    return str(base_url or "").rstrip("/")


def _endpoint_url(base_url, suffix, default_version_segment="/v1"):
    normalized = _normalized_base_url(base_url)
    parsed = urlparse(normalized)
    path = parsed.path.rstrip("/")
    if not path:
        path = default_version_segment
    path = f"{path}{suffix}"
    return urlunparse(parsed._replace(path=path))


def resolve_provider_protocol(provider_protocol, base_url, model_name):
    normalized = str(provider_protocol or AUTO_PROTOCOL).strip().lower() or AUTO_PROTOCOL
    if normalized in {OPENAI_CHAT_PROTOCOL, ANTHROPIC_MESSAGES_PROTOCOL}:
        return normalized
    if normalized != AUTO_PROTOCOL:
        raise ProviderRequestError(
            f"unsupported provider protocol: {provider_protocol}",
            error_code="PROVIDER_PROTOCOL_UNSUPPORTED",
        )

    lower_model = str(model_name or "").strip().lower()
    lower_base = str(base_url or "").strip().lower()
    if "claude" in lower_model or "anthropic" in lower_base:
        return ANTHROPIC_MESSAGES_PROTOCOL
    return OPENAI_CHAT_PROTOCOL


def build_openai_compatible_request(
    base_url,
    api_key,
    model_name,
    prompt_text,
    temperature=0,
    max_tokens=4000,
):
    body = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt_text}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    payload = json.dumps(body).encode("utf-8")
    return Request(
        url=_endpoint_url(base_url, "/chat/completions"),
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )


def build_openai_compatible_models_request(base_url, api_key):
    return Request(
        url=_endpoint_url(base_url, "/models"),
        headers={
            "Authorization": f"Bearer {api_key}",
        },
        method="GET",
    )


def build_anthropic_messages_request(
    base_url,
    api_key,
    model_name,
    prompt_text,
    temperature=0,
    max_tokens=4000,
):
    body = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt_text}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    payload = json.dumps(body).encode("utf-8")
    return Request(
        url=_endpoint_url(base_url, "/messages"),
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )


def build_anthropic_models_request(base_url, api_key):
    return Request(
        url=_endpoint_url(base_url, "/models"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="GET",
    )


def _extract_openai_compatible_text(data):
    choices = data.get("choices") or []
    if not choices:
        raise ProviderRequestError(
            "provider response missing choices",
            error_code="CONTRACT_INVALID",
        )
    message = choices[0].get("message") or {}
    text = message.get("content")
    if text is None or text == "":
        raise ProviderRequestError(
            "provider response missing assistant message content",
            error_code="CONTRACT_INVALID",
        )
    return text, choices[0].get("finish_reason", "unknown")


def _extract_anthropic_messages_text(data):
    content = data.get("content") or []
    if not content:
        raise ProviderRequestError(
            "provider response missing content blocks",
            error_code="CONTRACT_INVALID",
        )

    text_parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(str(block.get("text") or ""))
    text = "\n".join(part for part in text_parts if part).strip()
    if not text:
        raise ProviderRequestError(
            "provider response missing assistant text block",
            error_code="CONTRACT_INVALID",
        )
    return text, data.get("stop_reason", "unknown")


def build_response_contract_summary(data, protocol, finish_reason):
    if protocol == ANTHROPIC_MESSAGES_PROTOCOL:
        content_blocks = data.get("content") or []
        block_types = [
            str(block.get("type"))
            for block in content_blocks
            if isinstance(block, dict) and block.get("type")
        ]
    else:
        choices = data.get("choices") or []
        block_types = ["message"] if choices and (choices[0].get("message") or {}) else []

    return {
        "protocol": protocol,
        "response_id": data.get("id"),
        "returned_model": data.get("model"),
        "finish_reason": finish_reason,
        "content_block_types": block_types,
        "usage_keys": sorted((data.get("usage") or {}).keys()),
    }


def _http_error_code(exc):
    if exc.code in {401, 403}:
        return "AUTH_BLOCKED"
    if exc.code in {402, 408, 409, 429}:
        return "QUOTA_BLOCKED"
    if 500 <= exc.code <= 599:
        return "NETWORK_ERROR"
    return "PROVIDER_REQUEST_FAILED"


def _perform_request(request, timeout_s):
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout_s) as response:
            raw_json = json.loads(response.read().decode("utf-8"))
    except socket.timeout as exc:
        raise ProviderRequestError(str(exc), error_code="TIMEOUT") from exc
    except HTTPError as exc:
        raise ProviderRequestError(
            f"provider request failed with HTTP {exc.code}: {exc.reason}",
            error_code=_http_error_code(exc),
        ) from exc
    except URLError as exc:
        raise ProviderRequestError(
            f"provider request failed: {exc.reason}",
            error_code="NETWORK_ERROR",
        ) from exc
    except json.JSONDecodeError as exc:
        raise ProviderRequestError(
            "provider returned invalid JSON",
            error_code="CONTRACT_INVALID",
        ) from exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    return raw_json, latency_ms


def _extract_model_ids(data):
    candidates = data
    if isinstance(data, dict):
        candidates = data.get("data") or data.get("models") or []
    if not isinstance(candidates, list):
        raise ProviderRequestError(
            "provider models response must contain a model list",
            error_code="CONTRACT_INVALID",
        )

    model_ids = []
    for item in candidates:
        if isinstance(item, str):
            model_id = item.strip()
        elif isinstance(item, dict):
            model_id = str(item.get("id") or item.get("name") or "").strip()
        else:
            model_id = ""
        if model_id:
            model_ids.append(model_id)

    if not model_ids:
        raise ProviderRequestError(
            "provider models response did not include model ids",
            error_code="CONTRACT_INVALID",
        )
    return model_ids


def list_openai_compatible_models(base_url, api_key, timeout_s=30):
    request = build_openai_compatible_models_request(base_url=base_url, api_key=api_key)
    raw_json, latency_ms = _perform_request(request, timeout_s)
    models = _extract_model_ids(raw_json)
    return {
        "status": "ok",
        "protocol": OPENAI_CHAT_PROTOCOL,
        "latency_ms": latency_ms,
        "models": models,
        "model_count": len(models),
    }


def list_anthropic_models(base_url, api_key, timeout_s=30):
    request = build_anthropic_models_request(base_url=base_url, api_key=api_key)
    raw_json, latency_ms = _perform_request(request, timeout_s)
    models = _extract_model_ids(raw_json)
    return {
        "status": "ok",
        "protocol": ANTHROPIC_MESSAGES_PROTOCOL,
        "latency_ms": latency_ms,
        "models": models,
        "model_count": len(models),
    }


def list_provider_models(base_url, api_key, provider_protocol=AUTO_PROTOCOL, timeout_s=30):
    normalized = str(provider_protocol or AUTO_PROTOCOL).strip().lower() or AUTO_PROTOCOL
    if normalized == OPENAI_CHAT_PROTOCOL:
        return list_openai_compatible_models(base_url, api_key, timeout_s=timeout_s)
    if normalized == ANTHROPIC_MESSAGES_PROTOCOL:
        return list_anthropic_models(base_url, api_key, timeout_s=timeout_s)
    if normalized != AUTO_PROTOCOL:
        raise ProviderRequestError(
            f"unsupported provider protocol: {provider_protocol}",
            error_code="PROVIDER_PROTOCOL_UNSUPPORTED",
        )

    lower_base = str(base_url or "").strip().lower()
    attempts = (
        [list_anthropic_models, list_openai_compatible_models]
        if "anthropic" in lower_base
        else [list_openai_compatible_models, list_anthropic_models]
    )
    last_error = None
    for attempt in attempts:
        try:
            return attempt(base_url, api_key, timeout_s=timeout_s)
        except ProviderRequestError as exc:
            last_error = exc
    raise last_error


def run_openai_compatible_prompt(
    base_url,
    api_key,
    model_name,
    prompt_text,
    temperature=0,
    max_tokens=4000,
    timeout_s=60,
):
    request = build_openai_compatible_request(
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
        prompt_text=prompt_text,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    raw_json, latency_ms = _perform_request(request, timeout_s)
    response_text, finish_reason = _extract_openai_compatible_text(raw_json)
    contract = build_response_contract_summary(raw_json, OPENAI_CHAT_PROTOCOL, finish_reason)
    return {
        "status": "ok",
        "protocol": OPENAI_CHAT_PROTOCOL,
        "latency_ms": latency_ms,
        "finish_reason": finish_reason,
        "response_text": response_text,
        "usage": raw_json.get("usage", {}),
        "raw_json": raw_json,
        "response_contract_summary": contract,
    }


def run_anthropic_messages_prompt(
    base_url,
    api_key,
    model_name,
    prompt_text,
    temperature=0,
    max_tokens=4000,
    timeout_s=60,
):
    request = build_anthropic_messages_request(
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
        prompt_text=prompt_text,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    raw_json, latency_ms = _perform_request(request, timeout_s)
    response_text, finish_reason = _extract_anthropic_messages_text(raw_json)
    contract = build_response_contract_summary(
        raw_json,
        ANTHROPIC_MESSAGES_PROTOCOL,
        finish_reason,
    )
    return {
        "status": "ok",
        "protocol": ANTHROPIC_MESSAGES_PROTOCOL,
        "latency_ms": latency_ms,
        "finish_reason": finish_reason,
        "response_text": response_text,
        "usage": raw_json.get("usage", {}),
        "raw_json": raw_json,
        "response_contract_summary": contract,
    }


def run_provider_prompt(
    base_url,
    api_key,
    model_name,
    prompt_text,
    provider_protocol=AUTO_PROTOCOL,
    temperature=0,
    max_tokens=4000,
    timeout_s=60,
):
    resolved = resolve_provider_protocol(provider_protocol, base_url, model_name)
    if resolved == ANTHROPIC_MESSAGES_PROTOCOL:
        return run_anthropic_messages_prompt(
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            prompt_text=prompt_text,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
        )
    return run_openai_compatible_prompt(
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
        prompt_text=prompt_text,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_s=timeout_s,
    )
