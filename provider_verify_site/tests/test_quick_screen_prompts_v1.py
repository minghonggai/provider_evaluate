import unittest

from provider_verify_site.scripts.scorers_v1 import score_coding_fix
from provider_verify_site.scripts.task_pack_v1 import (
    get_agent_tool_use_task_pack,
    get_coding_probe_task_pack,
    get_factuality_calibration_task_pack,
    get_holdout_screen_task_pack,
    get_quick_screen_task_pack,
    get_screen_v2_task_pack,
)


class QuickScreenPromptTests(unittest.TestCase):
    def test_task_pack_includes_prompt_text_for_each_task(self):
        tasks = get_quick_screen_task_pack()

        self.assertEqual(len(tasks), 5)
        for task in tasks:
            self.assertIn("prompt", task)
            self.assertIsInstance(task["prompt"], str)
            self.assertTrue(task["prompt"].strip())

    def test_coding_task_includes_verifier_code(self):
        tasks = get_quick_screen_task_pack()
        coding_task = next(task for task in tasks if task["task_id"] == "coding_fix")

        self.assertIn("verifier_code", coding_task)
        self.assertIsInstance(coding_task["verifier_code"], str)
        self.assertIn("summarize_runs", coding_task["verifier_code"])

    def test_coding_prompt_requests_code_block_only(self):
        tasks = get_quick_screen_task_pack()
        coding_task = next(task for task in tasks if task["task_id"] == "coding_fix")

        self.assertIn("```python", coding_task["prompt"])
        self.assertIn("Return only one Python code block", coding_task["prompt"])

    def test_coding_probe_pack_contains_project_grounded_executable_task(self):
        tasks = get_coding_probe_task_pack()

        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task["task_id"], "project_grounded_coding")
        self.assertEqual(task["scorer"], "coding_fix")
        self.assertIn("model_evaluate", task["prompt"])
        self.assertIn("create_lite_gate_run", task["prompt"])
        self.assertIn("verifier_code", task)

    def test_agent_tool_use_pack_contains_schema_task(self):
        tasks = get_agent_tool_use_task_pack()

        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task["task_id"], "tool_plan_schema")
        self.assertEqual(task["scorer"], "tool_plan_schema")
        self.assertEqual(task["max_score"], 20)
        self.assertIn("JSON", task["prompt"])
        self.assertIn("selected_tool", task["prompt"])
        self.assertIn("should_execute", task["prompt"])

    def test_factuality_calibration_pack_contains_balanced_closed_context_tasks(self):
        tasks = get_factuality_calibration_task_pack()

        self.assertEqual(len(tasks), 8)
        self.assertEqual({task["scorer"] for task in tasks}, {"closed_context_factuality"})
        self.assertEqual({task["max_score"] for task in tasks}, {20})
        self.assertEqual({task["temperature"] for task in tasks}, {0})

        answerable = [
            task
            for task in tasks
            if task.get("answer_key", {}).get("expected_answer") != "NOT_IN_CONTEXT"
        ]
        unanswerable = [
            task
            for task in tasks
            if task.get("answer_key", {}).get("expected_answer") == "NOT_IN_CONTEXT"
        ]
        self.assertEqual(len(answerable), 4)
        self.assertEqual(len(unanswerable), 4)

        for task in tasks:
            self.assertTrue(task["task_id"].startswith("closed_context_factuality_"))
            self.assertIn("ANSWER:", task["prompt"])
            self.assertIn("EVIDENCE:", task["prompt"])
            self.assertIn("NOT_IN_CONTEXT", task["prompt"])
            self.assertIn("answer_key", task)
            self.assertIn("expected_answer", task["answer_key"])
            self.assertIn("acceptable_answers", task["answer_key"])
            self.assertIn("acceptable_evidence", task["answer_key"])

    def test_screen_v2_adds_reasoning_and_data_tasks(self):
        tasks = get_screen_v2_task_pack()

        self.assertEqual(len(tasks), 7)
        self.assertEqual(
            [task["task_id"] for task in tasks],
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
        reasoning_task = next(task for task in tasks if task["task_id"] == "reasoning_planning")
        data_task = next(task for task in tasks if task["task_id"] == "data_table_analysis")
        self.assertEqual(reasoning_task["scorer"], "reasoning_planning")
        self.assertEqual(data_task["scorer"], "data_table_analysis")
        self.assertIn("DECISION_ORDER", reasoning_task["prompt"])
        self.assertIn("CSV", data_task["prompt"])

    def test_holdout_screen_uses_independent_close_call_prompts(self):
        tasks = get_holdout_screen_task_pack()

        self.assertEqual(len(tasks), 7)
        self.assertEqual(
            [task["task_id"] for task in tasks],
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
        self.assertIn("sealed holdout", tasks[0]["prompt"].lower())
        self.assertIn("close-call holdout", tasks[3]["prompt"])
        self.assertIn("summarize_provider_success", tasks[5]["prompt"])
        self.assertIn("summarize_provider_success", tasks[5]["verifier_code"])

    def test_project_grounded_prompt_declares_verifier_contract(self):
        task = get_coding_probe_task_pack()[0]
        prompt = task["prompt"]

        self.assertIn('safe_filename("Opus 4.8 SYAPI") must return "opus_4_8_syapi"', prompt)
        self.assertIn('safe_filename("provider/v2 (test)") must return "provider_v2_test"', prompt)
        self.assertIn('reference_model must be exactly "opus 4.8 ai"', prompt)
        self.assertIn('judge must be exactly "human"', prompt)
        self.assertIn('Use exactly these Markdown section headings: "## Metadata"', prompt)

    def test_project_grounded_verifier_reports_actionable_assertion_note(self):
        task = get_coding_probe_task_pack()[0]
        response_text = """
```python
from pathlib import Path

def safe_filename(alias):
    return str(alias).replace(" ", "_")

def create_lite_gate_run(root, alias, pack, memory, now_iso, force=False):
    path = Path(root) / "lite_gate" / "runs" / now_iso[:10] / (safe_filename(alias) + "_raw_and_judge.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("## Metadata\\nreference_model: \\njudge: \\n", encoding="utf-8")
    return str(path)
```
"""

        result = score_coding_fix(response_text, verifier_code=task["verifier_code"])

        self.assertEqual(result["task_status"], "fail")
        self.assertIn("tests_failed", result["evidence_flags"])
        self.assertIn("safe_filename", result["notes"][0])

    def test_project_grounded_coding_verifier_accepts_working_solution(self):
        task = get_coding_probe_task_pack()[0]
        response_text = """
```python
import os
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
    if pack not in PACK_MAP:
        raise ValueError("unknown pack")
    if memory not in {"off", "unknown", "not_supported"}:
        raise ValueError("unknown memory")
    prompt_pack, task_count, prompt_file = PACK_MAP[pack]
    date = now_iso[:10]
    run_dir = Path(root) / "lite_gate" / "runs" / date
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"{safe_filename(alias)}_raw_and_judge.md"
    if path.exists() and not force:
        raise FileExistsError(f"file exists: {path}")
    content = (
        "# Lite Gate Run Record\\n\\n"
        "## Metadata\\n\\n"
        f"prompt_pack: {prompt_pack}\\n"
        f"task_count: {task_count}\\n"
        f"candidate_alias: \\"{alias}\\"\\n"
        "reference_model: opus 4.8 ai\\n"
        "fresh_context: true\\n"
        f"memory_status: {memory}\\n"
        "raw_output_saved_before_judging: false\\n"
        "saw_other_candidate_output: false\\n"
        "saw_prior_judge_notes: false\\n"
        "close_call_escalation: false\\n"
        "optional_code_probe_run: false\\n"
        "judge: human\\n"
        f"created_at: {now_iso}\\n"
        f"source_prompt_file: {prompt_file}\\n"
        "## Raw Candidate Output\\n\\n"
        "## Quick Judge\\n\\n"
        "label: HOLD\\n\\n"
        "## Reviewer Notes\\n"
    )
    path.write_text(content, encoding="utf-8")
    return str(path)
```
"""

        result = score_coding_fix(response_text, verifier_code=task["verifier_code"])

        self.assertEqual(result["task_status"], "pass")
        self.assertEqual(result["score"], 20)


if __name__ == "__main__":
    unittest.main()
