import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from provider_verify_site.scripts.auto_eval_storage import (
    build_route_metadata,
    build_run_manifest,
    build_route_key,
    ensure_run_workspace,
    persist_json_artifact,
    persist_text_artifact,
    persist_run_manifest,
    safe_slug,
    task_artifact_paths,
    unique_run_id,
)


class AutoEvalStorageTests(unittest.TestCase):
    def test_build_route_key_uses_protocol_host_and_model(self):
        self.assertEqual(
            build_route_key(
                provider_protocol="anthropic_messages",
                base_url="https://ai.hsuan.vip/",
                model_name="claude-opus-4-8",
            ),
            "anthropic_messages|ai.hsuan.vip|claude-opus-4-8",
        )
        self.assertEqual(
            build_route_key(
                provider_protocol="openai_chat",
                base_url="https://sakura886.site/v1",
                model_name="gpt-5.5",
            ),
            "openai_chat|sakura886.site|gpt-5.5",
        )

    def test_build_route_metadata_marks_unknown_host_uncomparable(self):
        metadata = build_route_metadata(
            provider_protocol="openai_chat",
            base_url="",
            model_name="gpt-5.5",
        )

        self.assertEqual(metadata["route_key"], "openai_chat|unknown-host|gpt-5.5")
        self.assertFalse(metadata["route_comparable"])
        self.assertEqual(metadata["base_url_host_hash"], "unknown")
        self.assertTrue(metadata["route_fingerprint"].startswith("uncomparable:"))

    def test_build_route_metadata_adds_host_hash_and_protocol_resolution(self):
        metadata = build_route_metadata(
            provider_protocol="auto",
            base_url="https://provider.example/v1",
            model_name="claude-opus-4-8",
            protocol_resolved="anthropic_messages",
        )

        self.assertEqual(metadata["route_key"], "auto|provider.example|claude-opus-4-8")
        self.assertEqual(metadata["protocol_resolved"], "anthropic_messages")
        self.assertEqual(len(metadata["base_url_host_hash"]), 16)
        self.assertTrue(metadata["route_comparable"])
        self.assertTrue(metadata["route_fingerprint"].startswith("rk1:"))

    def test_safe_slug_normalizes_alias(self):
        self.assertEqual(safe_slug("Opus 4.8 SYAPI"), "opus_4_8_syapi")
        self.assertEqual(safe_slug("provider/v2 (test)"), "provider_v2_test")
        self.assertEqual(safe_slug("一日"), "candidate")

    def test_unique_run_id_uses_timestamp_and_alias(self):
        now = datetime(2026, 6, 15, 9, 5, 0, tzinfo=timezone.utc).astimezone()

        run_id = unique_run_id("Opus 4.8 SYAPI", now=now)

        self.assertEqual(run_id, "2026-06-15-170500-opus_4_8_syapi")

    def test_ensure_run_workspace_creates_expected_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 6, 15, 9, 5, 0, tzinfo=timezone.utc).astimezone()
            run_id = unique_run_id("Opus 4.8 SYAPI", now=now)

            run_dir = ensure_run_workspace(root, run_id, now=now)
            self.assertTrue(run_dir.exists())
            self.assertEqual(
                run_dir.relative_to(root).as_posix(),
                "auto_eval_runs/2026-06-15/2026-06-15-170500-opus_4_8_syapi",
            )

    def test_build_and_persist_run_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 6, 15, 9, 5, 0, tzinfo=timezone.utc).astimezone()
            run_id = unique_run_id("Opus 4.8 SYAPI", now=now)
            run_dir = ensure_run_workspace(root, run_id, now=now)
            manifest = build_run_manifest(
                run_id=run_id,
                provider_alias="opus_4_8syapi",
                claimed_model="Claude Opus 4.8",
                model_name="claude-opus-4-8",
                base_url="https://provider.example/v1",
                provider_protocol="anthropic_messages",
                eval_mode="quick_screen_v1",
                task_ids=[
                    "boundary_safety",
                    "instruction_following",
                    "evidence_honesty",
                    "coding_fix",
                    "product_communication",
                ],
                now=now,
            )

            manifest_path = persist_run_manifest(run_dir, manifest)
            data = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest_path.name, "run_manifest.json")
        self.assertEqual(data["run_id"], run_id)
        self.assertEqual(data["status"], "created")
        self.assertEqual(data["provider_protocol"], "anthropic_messages")
        self.assertEqual(
            data["route_key"],
            "anthropic_messages|provider.example|claude-opus-4-8",
        )
        self.assertIn("route_fingerprint", data)
        self.assertTrue(data["route_comparable"])
        self.assertEqual(len(data["base_url_host_hash"]), 16)
        self.assertEqual(data["protocol_resolved"], "anthropic_messages")
        self.assertEqual(len(data["task_ids"]), 5)

    def test_task_artifact_paths_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 6, 15, 9, 5, 0, tzinfo=timezone.utc).astimezone()
            run_id = unique_run_id("Opus 4.8 SYAPI", now=now)
            run_dir = ensure_run_workspace(root, run_id, now=now)

            paths = task_artifact_paths(run_dir, "coding_fix")
            request_path = persist_json_artifact(paths["request_json"], {"task": "coding_fix"})
            response_text_path = persist_text_artifact(paths["response_text"], "raw response")
            score_path = persist_json_artifact(paths["score_json"], {"score": 18})

            request_data = json.loads(request_path.read_text(encoding="utf-8"))
            score_data = json.loads(score_path.read_text(encoding="utf-8"))

        self.assertEqual(request_path.name, "coding_fix_request.json")
        self.assertEqual(response_text_path.name, "coding_fix_response.txt")
        self.assertEqual(score_path.name, "coding_fix_score.json")
        self.assertEqual(request_data["task"], "coding_fix")
        self.assertEqual(score_data["score"], 18)


if __name__ == "__main__":
    unittest.main()
