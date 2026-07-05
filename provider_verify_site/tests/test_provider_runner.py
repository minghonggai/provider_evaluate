import json
import socket
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from provider_verify_site.scripts.provider_runner import (
    ProviderRequestError,
    build_anthropic_messages_request,
    build_openai_compatible_models_request,
    build_openai_compatible_request,
    list_provider_models,
    resolve_provider_protocol,
    run_anthropic_messages_prompt,
    run_openai_compatible_prompt,
)


class DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class ProviderRunnerTests(unittest.TestCase):
    def test_build_request_sets_headers_and_json_body(self):
        request = build_openai_compatible_request(
            base_url="https://provider.example/v1",
            api_key="secret-key",
            model_name="claude-opus-4-8",
            prompt_text="hello world",
            temperature=0,
            max_tokens=4000,
        )

        body = json.loads(request.data.decode("utf-8"))

        self.assertEqual(request.full_url, "https://provider.example/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-key")
        self.assertEqual(body["model"], "claude-opus-4-8")
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["max_tokens"], 4000)
        self.assertEqual(body["messages"][0]["content"], "hello world")

    def test_build_openai_models_request_sets_auth_header(self):
        request = build_openai_compatible_models_request(
            base_url="https://provider.example/v1",
            api_key="secret-key",
        )

        self.assertEqual(request.full_url, "https://provider.example/v1/models")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-key")
        self.assertEqual(request.get_method(), "GET")

    def test_build_anthropic_request_sets_headers_and_json_body(self):
        request = build_anthropic_messages_request(
            base_url="https://provider.example",
            api_key="secret-key",
            model_name="claude-opus-4-8",
            prompt_text="hello world",
            temperature=0,
            max_tokens=4000,
        )

        body = json.loads(request.data.decode("utf-8"))

        self.assertEqual(request.full_url, "https://provider.example/v1/messages")
        self.assertEqual(request.get_header("X-api-key"), "secret-key")
        self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")
        self.assertEqual(body["model"], "claude-opus-4-8")
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["max_tokens"], 4000)
        self.assertEqual(body["messages"][0]["content"], "hello world")

    def test_resolve_provider_protocol_auto_prefers_anthropic_for_claude_models(self):
        self.assertEqual(
            resolve_provider_protocol(
                provider_protocol="auto",
                base_url="https://provider.example",
                model_name="claude-opus-4-8",
            ),
            "anthropic_messages",
        )
        self.assertEqual(
            resolve_provider_protocol(
                provider_protocol="auto",
                base_url="https://provider.example/v1",
                model_name="gpt-5",
            ),
            "openai_chat",
        )

    @patch("provider_verify_site.scripts.provider_runner.urlopen")
    def test_list_provider_models_reads_openai_compatible_models(self, mock_urlopen):
        mock_urlopen.return_value = DummyResponse(
            {
                "object": "list",
                "data": [
                    {"id": "claude-opus-4-8"},
                    {"id": "gpt-5.5"},
                ],
            }
        )

        result = list_provider_models(
            base_url="https://provider.example/v1",
            api_key="secret-key",
            provider_protocol="auto",
        )

        self.assertEqual(result["protocol"], "openai_chat")
        self.assertEqual(result["models"], ["claude-opus-4-8", "gpt-5.5"])
        self.assertEqual(result["model_count"], 2)

    @patch("provider_verify_site.scripts.provider_runner.urlopen")
    def test_run_prompt_returns_normalized_response(self, mock_urlopen):
        mock_urlopen.return_value = DummyResponse(
            {
                "id": "chatcmpl-1",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "final answer"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46},
            }
        )

        result = run_openai_compatible_prompt(
            base_url="https://provider.example/v1",
            api_key="secret-key",
            model_name="claude-opus-4-8",
            prompt_text="hello world",
        )

        self.assertEqual(result["response_text"], "final answer")
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(result["usage"]["total_tokens"], 46)
        self.assertEqual(result["status"], "ok")
        self.assertIn("latency_ms", result)
        self.assertEqual(result["response_contract_summary"]["returned_model"], None)
        self.assertEqual(result["response_contract_summary"]["content_block_types"], ["message"])
        self.assertEqual(result["response_contract_summary"]["usage_keys"], ["completion_tokens", "prompt_tokens", "total_tokens"])

    @patch("provider_verify_site.scripts.provider_runner.urlopen")
    def test_run_anthropic_prompt_returns_normalized_response(self, mock_urlopen):
        mock_urlopen.return_value = DummyResponse(
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "hidden"},
                    {"type": "text", "text": "final answer"},
                ],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": 34},
            }
        )

        result = run_anthropic_messages_prompt(
            base_url="https://provider.example",
            api_key="secret-key",
            model_name="claude-opus-4-8",
            prompt_text="hello world",
        )

        self.assertEqual(result["response_text"], "final answer")
        self.assertEqual(result["finish_reason"], "end_turn")
        self.assertEqual(result["usage"]["output_tokens"], 34)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["protocol"], "anthropic_messages")
        self.assertEqual(result["response_contract_summary"]["response_id"], "msg_1")
        self.assertEqual(
            result["response_contract_summary"]["content_block_types"],
            ["thinking", "text"],
        )

    @patch("provider_verify_site.scripts.provider_runner.urlopen")
    def test_run_prompt_maps_timeout_to_provider_error(self, mock_urlopen):
        mock_urlopen.side_effect = socket.timeout("timed out")

        with self.assertRaises(ProviderRequestError) as ctx:
            run_openai_compatible_prompt(
                base_url="https://provider.example/v1",
                api_key="secret-key",
                model_name="claude-opus-4-8",
                prompt_text="hello world",
                timeout_s=10,
            )

        self.assertEqual(ctx.exception.error_code, "TIMEOUT")
        self.assertIn("timed out", str(ctx.exception))

    @patch("provider_verify_site.scripts.provider_runner.urlopen")
    def test_run_prompt_maps_http_error_to_provider_error(self, mock_urlopen):
        mock_urlopen.side_effect = HTTPError(
            url="https://provider.example/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=None,
        )

        with self.assertRaises(ProviderRequestError) as ctx:
            run_openai_compatible_prompt(
                base_url="https://provider.example/v1",
                api_key="secret-key",
                model_name="claude-opus-4-8",
                prompt_text="hello world",
            )

        self.assertEqual(ctx.exception.error_code, "AUTH_BLOCKED")
        self.assertIn("401", str(ctx.exception))

    @patch("provider_verify_site.scripts.provider_runner.urlopen")
    def test_run_prompt_maps_http_429_to_quota_blocked(self, mock_urlopen):
        mock_urlopen.side_effect = HTTPError(
            url="https://provider.example/v1/chat/completions",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=None,
        )

        with self.assertRaises(ProviderRequestError) as ctx:
            run_openai_compatible_prompt(
                base_url="https://provider.example/v1",
                api_key="secret-key",
                model_name="claude-opus-4-8",
                prompt_text="hello world",
            )

        self.assertEqual(ctx.exception.error_code, "QUOTA_BLOCKED")

    @patch("provider_verify_site.scripts.provider_runner.urlopen")
    def test_run_prompt_maps_url_error_to_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("name resolution failed")

        with self.assertRaises(ProviderRequestError) as ctx:
            run_openai_compatible_prompt(
                base_url="https://provider.example/v1",
                api_key="secret-key",
                model_name="claude-opus-4-8",
                prompt_text="hello world",
            )

        self.assertEqual(ctx.exception.error_code, "NETWORK_ERROR")

    @patch("provider_verify_site.scripts.provider_runner.urlopen")
    def test_run_prompt_rejects_missing_message_content(self, mock_urlopen):
        mock_urlopen.return_value = DummyResponse({"choices": []})

        with self.assertRaises(ProviderRequestError) as ctx:
            run_openai_compatible_prompt(
                base_url="https://provider.example/v1",
                api_key="secret-key",
                model_name="claude-opus-4-8",
                prompt_text="hello world",
            )

        self.assertEqual(ctx.exception.error_code, "CONTRACT_INVALID")


if __name__ == "__main__":
    unittest.main()
