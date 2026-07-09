# Factuality Calibration v1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a scoped `factuality_calibration_v1` lane that scores closed-context factual answers and correct abstentions as `factuality_score`.

**Architecture:** Reuse the existing scoped-eval pattern introduced for `agent_tool_use_v1`. Add a deterministic task pack, scorer, local-server profile mapping, report/schema/site-data support, and frontend labels without changing the default adaptive provider flow.

**Tech Stack:** Python standard library, `unittest`, local provider eval scripts, JSON schema, vanilla JavaScript frontend, `rtk` command wrapper.

## 中文评审摘要

这份实施计划用于把 `factuality_calibration_v1` 从设计落到代码，但当前文件本身不代表已经授权实现。

实施目标：

- 新增一个事实性校准评测模式：`factuality_calibration_v1`。
- 题目共 8 个，4 个材料内有答案，4 个材料内无答案。
- 评分结果只进入 `factuality_score`，不能并入 `capability_score`。
- 运行报告必须说明这只是短事实闭上下文证据，不证明开放世界事实、实时事实、模型身份、长上下文或生产可用性。

实施顺序：

1. 先写失败测试，确认现有系统还不支持这个 lane。
2. 再补任务包、评分器、本地服务、聚合层、schema、manifest 和前端展示。
3. 每个小阶段通过测试后再提交。
4. 最后跑完整本地验证。

评审重点：

- 是否认可“先测闭上下文事实性和拒答”，不做开放世界问答。
- 是否认可首版不引入 LLM judge，只做确定性评分。
- 是否认可该模式默认不出现在主 UI 选择器里，先作为受控扩展 lane。
- 是否认可任何 push、PR、真实 provider/API 运行都需要单独批准。

---

## Task 1: Add Factuality Task-Pack Tests

**Files:**
- Modify: `provider_verify_site/tests/test_quick_screen_prompts_v1.py`

**Step 1: Write the failing import**

Add `get_factuality_calibration_task_pack` to the existing import list from
`provider_verify_site.scripts.task_pack_v1`.

Expected failure before implementation: import error.

**Step 2: Add task-pack shape test**

Add this test:

```python
def test_factuality_calibration_pack_contains_balanced_closed_context_tasks(self):
    tasks = get_factuality_calibration_task_pack()

    self.assertEqual(len(tasks), 8)
    self.assertEqual({task["scorer"] for task in tasks}, {"closed_context_factuality"})
    self.assertEqual({task["max_score"] for task in tasks}, {20})
    self.assertEqual({task["temperature"] for task in tasks}, {0})

    answerable = [task for task in tasks if task.get("answer_key", {}).get("expected_answer") != "NOT_IN_CONTEXT"]
    unanswerable = [task for task in tasks if task.get("answer_key", {}).get("expected_answer") == "NOT_IN_CONTEXT"]
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
```

**Step 3: Run test to verify it fails**

Run:

```powershell
rtk python -m unittest provider_verify_site.tests.test_quick_screen_prompts_v1.QuickScreenPromptTests.test_factuality_calibration_pack_contains_balanced_closed_context_tasks -v
```

Expected: FAIL because `get_factuality_calibration_task_pack` does not exist.

**Step 4: Do not implement yet**

Commit only after the implementation in Task 2 passes.

## Task 2: Implement Factuality Task Pack

**Files:**
- Modify: `provider_verify_site/scripts/task_pack_v1.py`

**Step 1: Add prompt template helper**

Add a small helper near the other prompt constants:

```python
FACTUALITY_OUTPUT_CONTRACT = """Output exactly:
ANSWER: <short answer or NOT_IN_CONTEXT>
EVIDENCE: <exact supporting phrase from the passage or NOT_IN_CONTEXT>"""


def _closed_context_factuality_prompt(passage, question):
    return f"""Use only the passage below. Do not use outside knowledge.
If the answer is not stated in the passage, return NOT_IN_CONTEXT.

PASSAGE:
{passage}

QUESTION:
{question}

{FACTUALITY_OUTPUT_CONTRACT}
"""
```

**Step 2: Add 8 private local tasks**

Use project-local synthetic evidence packs, not public benchmark questions.

Example task objects:

```python
{
    "task_id": "closed_context_factuality_01",
    "scorer": "closed_context_factuality",
    "max_score": 20,
    "temperature": 0,
    "max_tokens": 220,
    "prompt": _closed_context_factuality_prompt(
        "Run alpha was created on 2026-07-02. Its final status was HOLD. "
        "The operator saved raw output before judging.",
        "What was the final status of run alpha?",
    ),
    "answer_key": {
        "expected_answer": "HOLD",
        "acceptable_answers": ["HOLD"],
        "acceptable_evidence": ["final status was HOLD"],
    },
}
```

Add four answerable tasks and four unanswerable tasks. For unanswerable tasks,
use:

```python
"expected_answer": "NOT_IN_CONTEXT",
"acceptable_answers": ["NOT_IN_CONTEXT"],
"acceptable_evidence": ["NOT_IN_CONTEXT"]
```

Keep task data small and ASCII-only.

**Step 3: Add public pack function**

Add near existing pack functions:

```python
def get_factuality_calibration_task_pack():
    return [
        # 8 task dictionaries
    ]
```

**Step 4: Run test to verify it passes**

Run:

```powershell
rtk python -m unittest provider_verify_site.tests.test_quick_screen_prompts_v1.QuickScreenPromptTests.test_factuality_calibration_pack_contains_balanced_closed_context_tasks -v
```

Expected: PASS.

**Step 5: Commit**

```powershell
rtk git add provider_verify_site/scripts/task_pack_v1.py provider_verify_site/tests/test_quick_screen_prompts_v1.py
rtk git commit -m "Add factuality calibration task pack"
```

## Task 3: Add Factuality Scorer Tests

**Files:**
- Modify: `provider_verify_site/tests/test_scorers_v1.py`

**Step 1: Add scorer import**

Import `score_closed_context_factuality`.

**Step 2: Add passing answer test**

```python
def test_closed_context_factuality_passes_exact_answer_and_evidence(self):
    answer_key = {
        "expected_answer": "HOLD",
        "acceptable_answers": ["HOLD"],
        "acceptable_evidence": ["final status was HOLD"],
    }
    result = score_closed_context_factuality(
        "ANSWER: HOLD\nEVIDENCE: final status was HOLD",
        answer_key,
    )

    self.assertEqual(result["task_status"], "pass")
    self.assertEqual(result["score"], 20)
```

**Step 3: Add partial evidence test**

```python
def test_closed_context_factuality_partial_for_correct_answer_with_missing_evidence(self):
    answer_key = {
        "expected_answer": "HOLD",
        "acceptable_answers": ["HOLD"],
        "acceptable_evidence": ["final status was HOLD"],
    }
    result = score_closed_context_factuality("ANSWER: HOLD\nEVIDENCE:", answer_key)

    self.assertEqual(result["task_status"], "fail")
    self.assertEqual(result["score"], 16)
    self.assertIn("missing_evidence", result["evidence_flags"])
```

**Step 4: Add abstention pass test**

```python
def test_closed_context_factuality_passes_not_in_context(self):
    answer_key = {
        "expected_answer": "NOT_IN_CONTEXT",
        "acceptable_answers": ["NOT_IN_CONTEXT"],
        "acceptable_evidence": ["NOT_IN_CONTEXT"],
    }
    result = score_closed_context_factuality(
        "ANSWER: NOT_IN_CONTEXT\nEVIDENCE: NOT_IN_CONTEXT",
        answer_key,
    )

    self.assertEqual(result["task_status"], "pass")
    self.assertEqual(result["score"], 20)
```

**Step 5: Add hallucination failure test**

```python
def test_closed_context_factuality_fails_hallucinated_answer_for_unanswerable_task(self):
    answer_key = {
        "expected_answer": "NOT_IN_CONTEXT",
        "acceptable_answers": ["NOT_IN_CONTEXT"],
        "acceptable_evidence": ["NOT_IN_CONTEXT"],
    }
    result = score_closed_context_factuality(
        "ANSWER: PASS\nEVIDENCE: The route probably passed.",
        answer_key,
    )

    self.assertEqual(result["task_status"], "fail")
    self.assertEqual(result["score"], 0)
    self.assertIn("wrong_abstention", result["evidence_flags"])
```

**Step 6: Add external-knowledge failure test**

```python
def test_closed_context_factuality_fails_external_knowledge_claim(self):
    answer_key = {
        "expected_answer": "HOLD",
        "acceptable_answers": ["HOLD"],
        "acceptable_evidence": ["final status was HOLD"],
    }
    result = score_closed_context_factuality(
        "ANSWER: HOLD\nEVIDENCE: I know from external data that it was HOLD.",
        answer_key,
    )

    self.assertEqual(result["task_status"], "fail")
    self.assertEqual(result["score"], 0)
    self.assertIn("external_knowledge_claim", result["evidence_flags"])
```

**Step 7: Run tests to verify failure**

Run:

```powershell
rtk python -m unittest provider_verify_site.tests.test_scorers_v1.ScorerTests.test_closed_context_factuality_passes_exact_answer_and_evidence provider_verify_site.tests.test_scorers_v1.ScorerTests.test_closed_context_factuality_partial_for_correct_answer_with_missing_evidence provider_verify_site.tests.test_scorers_v1.ScorerTests.test_closed_context_factuality_passes_not_in_context provider_verify_site.tests.test_scorers_v1.ScorerTests.test_closed_context_factuality_fails_hallucinated_answer_for_unanswerable_task provider_verify_site.tests.test_scorers_v1.ScorerTests.test_closed_context_factuality_fails_external_knowledge_claim -v
```

Expected: FAIL because scorer does not exist.

## Task 4: Implement Factuality Scorer

**Files:**
- Modify: `provider_verify_site/scripts/scorers_v1.py`

**Step 1: Add labeled-value extraction helper if reusable helper is absent**

If there is already a suitable helper, reuse it. Otherwise add:

```python
def _extract_line_value(text, label):
    match = re.search(rf"^\s*{re.escape(label)}\s*:\s*(.*)$", text or "", flags=re.MULTILINE)
    return match.group(1).strip() if match else ""
```

**Step 2: Add normalization helpers**

```python
def _normalize_factuality_value(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).strip(" .").lower()


def _matches_any(value, allowed):
    normalized = _normalize_factuality_value(value)
    return any(normalized == _normalize_factuality_value(item) for item in allowed or [])
```

**Step 3: Add external-knowledge detector**

```python
def _claims_external_knowledge(text):
    lowered = (text or "").lower()
    phrases = [
        "external data",
        "outside knowledge",
        "from the web",
        "i browsed",
        "i searched",
        "real-time",
        "latest information",
    ]
    return any(phrase in lowered for phrase in phrases)
```

**Step 4: Add scorer**

```python
def score_closed_context_factuality(response_text, answer_key=None):
    answer_key = answer_key or {}
    text = (response_text or "").strip()
    flags = []
    notes = []

    if _claims_external_knowledge(text):
        return _result(
            0,
            "fail",
            evidence_flags=["external_knowledge_claim"],
            notes=["response claims support beyond the supplied passage"],
        )

    answer = _extract_line_value(text, "ANSWER")
    evidence = _extract_line_value(text, "EVIDENCE")
    if not answer or not evidence:
        return _result(
            0,
            "fail",
            evidence_flags=["invalid_format"],
            notes=["response must include ANSWER and EVIDENCE lines"],
        )

    expected = answer_key.get("expected_answer", "")
    acceptable_answers = answer_key.get("acceptable_answers") or [expected]
    acceptable_evidence = answer_key.get("acceptable_evidence") or []

    expected_is_abstain = _normalize_factuality_value(expected) == "not_in_context"
    answer_is_abstain = _normalize_factuality_value(answer) == "not_in_context"
    evidence_is_abstain = _normalize_factuality_value(evidence) == "not_in_context"

    if expected_is_abstain:
        if answer_is_abstain and evidence_is_abstain:
            return _result(20, "pass")
        flags.append("wrong_abstention")
        notes.append("task is unanswerable from passage but response gave an answer")
        return _result(0, "fail", evidence_flags=flags, notes=notes)

    if not _matches_any(answer, acceptable_answers):
        flags.append("wrong_answer")
        notes.append("answer does not match the expected closed-context answer")
        return _result(0, "fail", evidence_flags=flags, notes=notes)

    if not evidence or evidence_is_abstain:
        flags.append("missing_evidence")
        notes.append("answer is correct but supporting evidence is missing")
        return _result(16, "fail", evidence_flags=flags, notes=notes)

    if not _matches_any(evidence, acceptable_evidence):
        flags.append("missing_evidence")
        notes.append("answer is correct but evidence is not an exact accepted phrase")
        return _result(16, "fail", evidence_flags=flags, notes=notes)

    return _result(20, "pass")
```

**Step 5: Run tests to verify pass**

Run the same targeted scorer command from Task 3.

Expected: PASS.

**Step 6: Commit**

```powershell
rtk git add provider_verify_site/scripts/scorers_v1.py provider_verify_site/tests/test_scorers_v1.py
rtk git commit -m "Add closed-context factuality scorer"
```

## Task 5: Wire Local Eval Server

**Files:**
- Modify: `provider_verify_site/scripts/local_eval_server.py`
- Modify: `provider_verify_site/tests/test_local_eval_server.py`

**Step 1: Add tests for mode validation and run fields**

In `test_local_eval_server.py`, add a sample payload helper mirroring the
existing `sample_agent_tool_use_payload()` pattern:

```python
def sample_factuality_payload(self):
    payload = self.sample_quick_screen_payload()
    payload["eval_mode"] = "factuality_calibration_v1"
    return payload
```

Add validation test:

```python
def test_validate_quick_screen_payload_accepts_factuality_mode(self):
    normalized = validate_quick_screen_payload(self.sample_factuality_payload())

    self.assertEqual(normalized["eval_mode"], "factuality_calibration_v1")
```

Add run test based on the agent-tool-use run test. Patch provider responses to
return 8 valid task responses. Assert:

```python
self.assertEqual(run_report["eval_mode"], "factuality_calibration_v1")
self.assertEqual(run_report["eval_profile"], "factuality_calibration")
self.assertEqual(run_report["verdict_scope"], "factuality_calibration")
self.assertEqual(run_report["factuality_score"], 100)
self.assertIsNone(run_report["capability_score"])
self.assertEqual(run_report["capability_tier"], "TIER_UNKNOWN")
self.assertEqual(run_report["coverage_map"]["factuality"], "standard")
self.assertEqual(readback["factuality_score"], 100)
```

**Step 2: Run targeted tests to verify failure**

Run:

```powershell
rtk python -m unittest provider_verify_site.tests.test_local_eval_server.LocalEvalServerTests.test_validate_quick_screen_payload_accepts_factuality_mode -v
```

Expected: FAIL because mode is not accepted.

**Step 3: Implement server constants and imports**

Add import:

```python
get_factuality_calibration_task_pack,
```

Add constant:

```python
FACTUALITY_CALIBRATION_EVAL_MODE = "factuality_calibration_v1"
```

Include it in `EVAL_MODES`.

**Step 4: Add coverage and not-proven semantics**

In `_base_coverage_map()`, add:

```python
"factuality": "not_tested",
```

In `_coverage_map_for_eval_mode()`:

```python
elif eval_mode == FACTUALITY_CALIBRATION_EVAL_MODE:
    coverage["factuality"] = "standard"
```

In `_not_proven_for_eval_mode()`:

```python
if eval_mode == FACTUALITY_CALIBRATION_EVAL_MODE:
    return [
        "full general capability",
        "open-world factual knowledge",
        "factual freshness beyond supplied evidence",
        "long-form factuality",
        "provider identity",
        "long-context capability",
        "multi-session route stability",
        "production suitability",
    ]
```

**Step 5: Add decision and profile fields**

In `_decision_v2()`, treat factuality like other scoped runs:

```python
if eval_mode == FACTUALITY_CALIBRATION_EVAL_MODE:
    score = int(summary.get("capability_score") or 0)
    if score >= 85:
        return "TRIAL_RECOMMENDED"
    if score >= 70:
        return "LIMITED_USE"
    return "NOT_RECOMMENDED"
```

In `_profile_scope_fields()`:

```python
if eval_mode == FACTUALITY_CALIBRATION_EVAL_MODE:
    return {
        **common,
        "eval_profile": "factuality_calibration",
        "verdict_scope": "factuality_calibration",
        "factuality_score": summary["capability_score"],
        "screen_score": None,
        "coding_axis_score": None,
        "agent_tool_use_score": None,
        "capability_score": None,
        "capability_tier": "TIER_UNKNOWN",
    }
```

Ensure all other profile branches include `factuality_score: None` if tests or
readbacks require explicit nulls.

**Step 6: Route task pack**

In `create_quick_screen_run`, select:

```python
elif normalized["eval_mode"] == FACTUALITY_CALIBRATION_EVAL_MODE:
    task_pack = get_factuality_calibration_task_pack()
```

**Step 7: Ensure task scoring passes answer key**

Find the scoring dispatch path. If scorer functions currently receive only
`response_text`, adapt the dispatch for this scorer:

```python
if task["scorer"] == "closed_context_factuality":
    score_result = scorer(response_text, task.get("answer_key"))
else:
    score_result = scorer(response_text)
```

Keep existing verifier-code behavior unchanged.

**Step 8: Add readback/list fields**

Where readback and list responses include scoped scores, add:

```python
"factuality_score": report.get("factuality_score"),
```

and manifest fallback:

```python
"factuality_score": manifest.get("factuality_score"),
```

**Step 9: Run targeted tests**

Run:

```powershell
rtk python -m unittest provider_verify_site.tests.test_local_eval_server.LocalEvalServerTests.test_validate_quick_screen_payload_accepts_factuality_mode -v
```

Then run the new run-field test.

Expected: PASS.

**Step 10: Commit**

```powershell
rtk git add provider_verify_site/scripts/local_eval_server.py provider_verify_site/tests/test_local_eval_server.py
rtk git commit -m "Wire factuality calibration local eval mode"
```

## Task 6: Wire Aggregation And Site Data

**Files:**
- Modify: `provider_verify_site/scripts/decision_aggregator_v1.py`
- Modify: `provider_verify_site/scripts/build_site_data.py`
- Modify: `provider_verify_site/tests/test_build_site_data.py`

**Step 1: Add build-site-data test**

Add test similar to
`test_build_report_reads_agent_tool_use_score_as_scoped_evidence`.

Expected provider assertions:

```python
self.assertEqual(provider["eval_profile"], "factuality_calibration")
self.assertEqual(provider["verdict_scope"], "factuality_calibration")
self.assertEqual(provider["factuality_score"], 100)
self.assertIsNone(provider["capability_score"])
self.assertEqual(provider["capability_tier"], "TIER_UNKNOWN")
self.assertEqual(provider["coverage_map"]["factuality"], "standard")
self.assertEqual(provider["score_groups"]["factuality"]["task_count"], 8)
self.assertEqual(provider["score_groups"]["factuality"]["score"], 100)
```

**Step 2: Run test to verify failure**

Run the new targeted test.

Expected: FAIL because factuality profile and score fields are not yet known.

**Step 3: Update task groups**

In `decision_aggregator_v1.py`, add:

```python
FACTUALITY_TASK_IDS = {
    "closed_context_factuality_01",
    "closed_context_factuality_02",
    "closed_context_factuality_03",
    "closed_context_factuality_04",
    "closed_context_factuality_05",
    "closed_context_factuality_06",
    "closed_context_factuality_07",
    "closed_context_factuality_08",
}
```

Include:

```python
"factuality": _score_group(task_results, FACTUALITY_TASK_IDS)
```

In `build_site_data.py`, add matching task IDs and include:

```python
"factuality": score_group_from_tasks(task_results, FACTUALITY_TASK_IDS)
```

**Step 4: Update scoped profile sets**

In `build_site_data.py`:

```python
SCOPED_EVAL_PROFILES = {"screen", "coding_only", "agent_tool_use", "factuality_calibration"}
FACTUALITY_CALIBRATION_EVAL_MODES = {"factuality_calibration_v1"}
```

Update `infer_eval_profile()` and `infer_verdict_scope()`.

**Step 5: Add default coverage and not-proven fields**

Add `factuality` to `base_coverage_map()`.

Add default coverage:

```python
elif eval_mode in FACTUALITY_CALIBRATION_EVAL_MODES:
    coverage["factuality"] = "standard"
```

Add default not-proven list consistent with the design doc.

**Step 6: Preserve factuality score**

Where provider payload values are read:

```python
factuality_score = int_or_none(payload.get("factuality_score"))
```

Ensure provider output includes:

```python
"factuality_score": factuality_score,
```

Ensure scoped profiles keep:

```python
provider["capability_score"] = None
provider["capability_tier"] = "TIER_UNKNOWN"
```

**Step 7: Update decision helper signature**

If `default_decision_v2()` uses explicit score parameters, add optional
`factuality_score=None` and factuality branch.

**Step 8: Run targeted test**

Run the new build-site-data test.

Expected: PASS.

**Step 9: Commit**

```powershell
rtk git add provider_verify_site/scripts/decision_aggregator_v1.py provider_verify_site/scripts/build_site_data.py provider_verify_site/tests/test_build_site_data.py
rtk git commit -m "Add factuality score aggregation"
```

## Task 7: Update Schema And Manifest

**Files:**
- Modify: `provider_verify_site/data/schema_v1.json`
- Modify: `provider_verify_site/eval_tasks/manifest_v1.json`
- Modify: `provider_verify_site/tests/test_build_site_data.py`

**Step 1: Extend schema test**

Update existing schema/manifest tests to assert:

```python
self.assertIn("factuality_score", provider_props)
self.assertIn("factuality_calibration", provider_props["eval_profile"]["enum"])
self.assertIn("factuality_calibration_v1", provider_props["capability_status"]["enum"])
```

Add manifest assertions:

```python
self.assertEqual(profiles["factuality_calibration_v1"]["status"], "implemented_initial")
self.assertEqual(profiles["factuality_calibration_v1"]["task_count"], 8)
```

**Step 2: Run test to verify failure**

Run relevant schema/manifest tests in `test_build_site_data.py`.

Expected: FAIL before schema and manifest edits.

**Step 3: Update schema**

In provider properties:

```json
"factuality_score": { "type": ["integer", "null"] }
```

Add `factuality_calibration` to `eval_profile` enum.

Add `factuality_calibration_v1` to `capability_status` enum if eval modes are
represented there.

**Step 4: Update manifest**

Change profile:

```json
{
  "profile_id": "factuality_calibration_v1",
  "status": "implemented_initial",
  "score_scope": "factuality_score",
  "may_emit_capability_score": false,
  "task_count": 8,
  "recommended_task_count": "8-15",
  "source_file": "provider_verify_site/scripts/task_pack_v1.py"
}
```

Add implemented tasks for the 8 factuality tasks, or mark
`closed_context_factuality_v1` as implemented initial with the concrete task ID
list. Keep public benchmark questions out of the manifest.

**Step 5: Validate JSON**

Run:

```powershell
rtk python -m json.tool provider_verify_site/data/schema_v1.json
rtk python -m json.tool provider_verify_site/eval_tasks/manifest_v1.json
```

Expected: both PASS.

**Step 6: Run targeted tests**

Run the updated schema/manifest tests.

Expected: PASS.

**Step 7: Commit**

```powershell
rtk git add provider_verify_site/data/schema_v1.json provider_verify_site/eval_tasks/manifest_v1.json provider_verify_site/tests/test_build_site_data.py
rtk git commit -m "Register factuality calibration schema"
```

## Task 8: Update Frontend Display

**Files:**
- Modify: `provider_verify_site/app.js`
- Modify: `provider_verify_site/tests/test_quick_screen_frontend.py`

**Step 1: Add frontend tests**

Add a test similar to agent tool-use frontend label coverage:

```python
def test_app_labels_factuality_scope_without_default_mode_entry(self):
    js = APP_JS.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")

    self.assertIn("factuality_calibration", js)
    self.assertIn("factuality_score", js)
    self.assertIn("factuality_calibration_v1", js)
    self.assertNotIn('value="factuality_calibration_v1"', html)
```

Add a score selection test so factuality scores appear in detail/table score
fallbacks but do not become `capability_score`.

**Step 2: Run tests to verify failure**

Run:

```powershell
rtk python -m unittest provider_verify_site.tests.test_quick_screen_frontend.QuickScreenFrontendTests.test_app_labels_factuality_scope_without_default_mode_entry -v
```

Expected: FAIL before frontend updates.

**Step 3: Add bilingual labels**

Add Chinese and English copy entries for:

- factuality profile label
- factuality score label
- `factuality_calibration_v1` mode label
- score scope label

Example English copy:

```javascript
"mode.factuality_calibration_v1.label": "Factuality Calibration",
"scope.factuality_calibration": "Factuality calibration",
"detail.factualityScore": "Factuality score",
```

For Chinese copy, follow the existing bilingual label pattern in `app.js`.
Keep the stable i18n keys below and choose final user-facing Chinese wording
during implementation review:

```javascript
"mode.factuality_calibration_v1.label": "<Chinese factuality calibration label>",
"scope.factuality_calibration": "<Chinese factuality calibration scope>",
"detail.factualityScore": "<Chinese factuality score label>",
```

**Step 4: Update score fallback helpers**

Where the app chooses a scoped score, include:

```javascript
item.factuality_score
```

Where scope is inferred:

```javascript
if (item.factuality_score !== null && item.factuality_score !== undefined) {
  return item.verdict_scope && item.verdict_scope !== "unknown" ? item.verdict_scope : "factuality_calibration";
}
```

Where detail fields are rendered, add `factuality_score`.

**Step 5: Keep out of default UI selector**

Do not add `factuality_calibration_v1` as a visible default mode option unless
the product decision changes. This mirrors `agent_tool_use_v1`.

**Step 6: Run frontend tests**

Run:

```powershell
rtk python -m unittest provider_verify_site.tests.test_quick_screen_frontend -v
rtk node --check provider_verify_site/app.js
```

Expected: PASS.

**Step 7: Commit**

```powershell
rtk git add provider_verify_site/app.js provider_verify_site/tests/test_quick_screen_frontend.py
rtk git commit -m "Show factuality calibration scope"
```

## Task 9: Full Verification

**Files:**
- No edits expected.

**Step 1: Run full test suite**

Run:

```powershell
rtk python -m unittest discover -s provider_verify_site/tests -v
```

Expected: all tests PASS.

**Step 2: Run compile and syntax checks**

Run:

```powershell
rtk python -m py_compile provider_verify_site/scripts/task_pack_v1.py provider_verify_site/scripts/local_eval_server.py provider_verify_site/scripts/decision_aggregator_v1.py provider_verify_site/scripts/scorers_v1.py provider_verify_site/scripts/provider_runner.py provider_verify_site/scripts/build_site_data.py provider_verify_site/scripts/auto_eval_storage.py
rtk node --check provider_verify_site/app.js
rtk python -m json.tool provider_verify_site/data/schema_v1.json
rtk python -m json.tool provider_verify_site/eval_tasks/manifest_v1.json
rtk git diff --check
```

Expected: all checks PASS.

**Step 3: Inspect git status**

Run:

```powershell
rtk git status --short --branch --untracked-files=all
```

Expected: clean working tree on the feature branch, unless intentionally
uncommitted handoff docs remain.

**Step 4: Prepare integration summary**

Summarize:

- commits created
- tests run
- score semantics
- boundaries not crossed
- no live provider/API calls
- no secrets read or stored
- no push/PR unless separately approved

## Task 10: Optional PR/Publication Gate

**Files:**
- No edits unless user approves publication.

**Step 1: Stop for approval**

Do not push, open a PR, tag, release, or delete branches unless the user gives
an explicit publication approval.

Recommended approval request should state:

- local implementation is complete
- verification status
- remote impact
- no real run data or secrets included
- exact branch and PR action proposed
