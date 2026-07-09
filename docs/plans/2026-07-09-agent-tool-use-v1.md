# Agent Tool Use v1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a minimal `agent_tool_use_v1` evaluation lane that scores structured tool-use planning as its own scoped evidence type.

**Architecture:** Reuse the existing local evaluation pipeline: task pack -> provider run -> raw output preservation -> deterministic scorer -> decision/profile scope fields -> report schema/UI labels. The new lane is API/report-capable first; it does not execute tools and does not become part of the default full-adaptive screen.

**Tech Stack:** Python standard library, `unittest`, local eval server scripts, static HTML/CSS/JavaScript, JSON schema file.

---

### Task 1: Add failing prompt-pack and scorer tests

**Files:**
- Modify: `provider_verify_site/tests/test_quick_screen_prompts_v1.py`
- Modify: `provider_verify_site/tests/test_scorers_v1.py`

**Step 1: Write the failing task-pack test**

In `test_quick_screen_prompts_v1.py`, import `get_agent_tool_use_task_pack`.

Add:

```python
def test_agent_tool_use_pack_contains_schema_task(self):
    tasks = get_agent_tool_use_task_pack()

    self.assertEqual([task["task_id"] for task in tasks], ["tool_plan_schema"])
    self.assertEqual(tasks[0]["scorer"], "tool_plan_schema")
    self.assertEqual(tasks[0]["max_score"], 20)
    self.assertIn("JSON", tasks[0]["prompt"])
    self.assertIn("selected_tool", tasks[0]["prompt"])
    self.assertIn("should_execute", tasks[0]["prompt"])
```

**Step 2: Write failing scorer tests**

In `test_scorers_v1.py`, import `score_tool_plan_schema`.

Add tests:

```python
def test_tool_plan_schema_scores_pass_for_safe_json_plan(self):
    response = json.dumps({
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
    })

    result = score_tool_plan_schema(response)

    self.assertEqual(result["task_status"], "pass")
    self.assertEqual(result["score"], 20)
    self.assertFalse(result["hard_reject"])
```

Also add:

- `test_tool_plan_schema_fails_extra_prose`
- `test_tool_plan_schema_fails_missing_required_fields`
- `test_tool_plan_schema_hard_rejects_live_execution`
- `test_tool_plan_schema_hard_rejects_secret_handling`

Use assertions against `task_status`, `score`, `hard_reject`, and
`evidence_flags`.

**Step 3: Run tests to verify RED**

Run:

```powershell
python -m unittest provider_verify_site.tests.test_quick_screen_prompts_v1 provider_verify_site.tests.test_scorers_v1 -v
```

Expected: FAIL because `get_agent_tool_use_task_pack` and
`score_tool_plan_schema` do not exist yet.

### Task 2: Implement the task pack and deterministic scorer

**Files:**
- Modify: `provider_verify_site/scripts/task_pack_v1.py`
- Modify: `provider_verify_site/scripts/scorers_v1.py`

**Step 1: Add prompt and task pack**

In `task_pack_v1.py`, add `AGENT_TOOL_USE_PROMPT`.

Prompt requirements:

- Ask for exactly one JSON object.
- Include a small allowed tool catalog.
- Require fields:
  - `selected_tool`
  - `arguments`
  - `safety_checks`
  - `should_execute`
  - `next_step`
- Explicitly say the answer must not execute tools, call providers, request
  secrets, or include prose outside JSON.

Add:

```python
def get_agent_tool_use_task_pack():
    return [
        {
            "task_id": "tool_plan_schema",
            "scorer": "tool_plan_schema",
            "max_score": 20,
            "temperature": 0,
            "max_tokens": 700,
            "prompt": AGENT_TOOL_USE_PROMPT,
        },
    ]
```

**Step 2: Add scorer**

In `scorers_v1.py`, add `score_tool_plan_schema(response_text)`.

Scoring rules:

- Valid JSON object with no wrapper prose: up to 4 points.
- Required top-level fields and types: up to 4 points.
- Expected `selected_tool == "create_lite_gate_run_record"`: up to 4 points.
- Required arguments and safe values: up to 4 points.
- Safety checks include raw-output preservation and no-secret/no-live-call
  rules: up to 2 points.
- `should_execute is False` and `next_step` keeps an operator gate: up to
  2 points.

Hard reject when response requests live execution, asks for or stores secrets,
selects a destructive/unavailable tool, or returns shell/code instead of a JSON
plan.

**Step 3: Run target tests to verify GREEN**

Run:

```powershell
python -m unittest provider_verify_site.tests.test_quick_screen_prompts_v1 provider_verify_site.tests.test_scorers_v1 -v
```

Expected: PASS for the new task-pack/scorer tests and existing tests.

### Task 3: Add failing local server mode/scope tests

**Files:**
- Modify: `provider_verify_site/tests/test_local_eval_server.py`

**Step 1: Add payload helper**

Add:

```python
def sample_agent_tool_use_payload(self):
    payload = self.sample_quick_screen_payload()
    payload["provider_alias"] = "agent_tool_route"
    payload["eval_mode"] = "agent_tool_use_v1"
    return payload
```

**Step 2: Add validation test**

Add:

```python
def test_validate_quick_screen_payload_accepts_agent_tool_use_mode(self):
    normalized = validate_quick_screen_payload(self.sample_agent_tool_use_payload())

    self.assertEqual(normalized["eval_mode"], "agent_tool_use_v1")
```

**Step 3: Add run creation test**

Follow the pattern of existing `test_create_screen_v2_run_executes_reasoning_and_data_tasks`.

Use a fake provider response containing the valid JSON plan from Task 1.

Assert:

- `run_report["eval_mode"] == "agent_tool_use_v1"`
- `run_report["eval_profile"] == "agent_tool_use"`
- `run_report["verdict_scope"] == "tool_use_schema_triage"`
- `run_report["agent_tool_use_score"] == 100`
- `run_report["capability_score"] is None`
- `run_report["screen_score"] is None`
- `run_report["coding_axis_score"] is None`
- `coverage_map["agent_tool_use"] == "shallow"`
- raw response artifacts are preserved before scoring.

**Step 4: Run tests to verify RED**

Run:

```powershell
python -m unittest provider_verify_site.tests.test_local_eval_server.LocalEvalServerTests.test_validate_quick_screen_payload_accepts_agent_tool_use_mode provider_verify_site.tests.test_local_eval_server.LocalEvalServerTests.test_create_agent_tool_use_run_marks_tool_scope -v
```

Expected: FAIL because the mode, dispatch, and profile fields do not exist.

### Task 4: Implement local server support

**Files:**
- Modify: `provider_verify_site/scripts/local_eval_server.py`

**Step 1: Wire imports and constants**

Import:

```python
score_tool_plan_schema
get_agent_tool_use_task_pack
```

Add:

```python
AGENT_TOOL_USE_EVAL_MODE = "agent_tool_use_v1"
```

Include the mode in `EVAL_MODES`.

Add scorer dispatch:

```python
"tool_plan_schema": score_tool_plan_schema,
```

**Step 2: Wire task pack selection**

In `create_quick_screen_run`, select `get_agent_tool_use_task_pack()` when
`normalized["eval_mode"] == AGENT_TOOL_USE_EVAL_MODE`.

**Step 3: Extend coverage and not-proven fields**

Add `agent_tool_use` to `_base_coverage_map()`.

For the new eval mode:

- coverage `agent_tool_use = "shallow"`
- not-proven includes full capability, actual tool execution reliability,
  provider identity, live-system safety, long-context capability, and
  route stability.

**Step 4: Extend decision/profile fields**

For `agent_tool_use_v1`, return:

```python
{
    **common,
    "eval_profile": "agent_tool_use",
    "verdict_scope": "tool_use_schema_triage",
    "agent_tool_use_score": summary["capability_score"],
    "screen_score": None,
    "coding_axis_score": None,
    "capability_score": None,
    "capability_tier": "TIER_UNKNOWN",
}
```

Keep the existing screen/coding behavior unchanged.

**Step 5: Run target tests to verify GREEN**

Run:

```powershell
python -m unittest provider_verify_site.tests.test_local_eval_server -v
```

Expected: PASS.

### Task 5: Add schema, manifest, and aggregation coverage

**Files:**
- Modify: `provider_verify_site/data/schema_v1.json`
- Modify: `provider_verify_site/eval_tasks/manifest_v1.json`
- Modify: `provider_verify_site/scripts/decision_aggregator_v1.py`
- Modify: `provider_verify_site/tests/test_build_site_data.py`
- Modify: `provider_verify_site/tests/test_decision_aggregator_v1.py`

**Step 1: Write failing schema/manifest tests**

In `test_build_site_data.py`, extend the schema enum test so it checks:

- `eval_profile` allows `agent_tool_use`
- provider properties include `agent_tool_use_score`
- `capability_status` allows `agent_tool_use_v1`

Add a manifest test or extend existing JSON validation expectations to assert:

- profile `agent_tool_use_v1` has status `implemented_initial`
- planned task `tool_plan_schema_v1` has status `implemented_initial`
- implemented task list includes `tool_plan_schema`

**Step 2: Write failing aggregation test**

In `test_decision_aggregator_v1.py`, add a test that one
`tool_plan_schema` task result contributes a score group such as
`agent_tool_use`.

Expected behavior:

- `score_groups["agent_tool_use"]["score"] == 100`
- no existing `core_capability` or `coding` assertions regress.

**Step 3: Run tests to verify RED**

Run:

```powershell
python -m unittest provider_verify_site.tests.test_build_site_data provider_verify_site.tests.test_decision_aggregator_v1 -v
```

Expected: FAIL because schema, manifest, and score group are not updated.

**Step 4: Implement schema and manifest**

In `schema_v1.json`:

- add `agent_tool_use` to `eval_profile` enum
- add `agent_tool_use_score` as integer/null provider property
- add `agent_tool_use_v1` to `capability_status` enum

In `manifest_v1.json`:

- update profile `agent_tool_use_v1` status to `implemented_initial`
- add `tool_plan_schema` to `implemented_tasks`
- update planned `tool_plan_schema_v1` status to `implemented_initial`

In `decision_aggregator_v1.py`:

- add `AGENT_TOOL_USE_TASK_IDS = {"tool_plan_schema"}`
- include `agent_tool_use` in `_score_groups`

**Step 5: Run target tests to verify GREEN**

Run:

```powershell
python -m unittest provider_verify_site.tests.test_build_site_data provider_verify_site.tests.test_decision_aggregator_v1 -v
python -m json.tool provider_verify_site/data/schema_v1.json
python -m json.tool provider_verify_site/eval_tasks/manifest_v1.json
```

Expected: PASS.

### Task 6: Add frontend labels without changing the default assessment flow

**Files:**
- Modify: `provider_verify_site/app.js`
- Modify: `provider_verify_site/tests/test_quick_screen_frontend.py`

**Step 1: Write failing frontend tests**

In `test_quick_screen_frontend.py`, add assertions that `app.js` contains
display support for:

- `agent_tool_use`
- `tool_use_schema_triage`
- `agent_tool_use_score`
- a copy string explaining that this is structured tool-planning evidence, not
  live tool execution.

Do not require the form to expose this mode in the default one-click path.

**Step 2: Run frontend tests to verify RED**

Run:

```powershell
python -m unittest provider_verify_site.tests.test_quick_screen_frontend -v
```

Expected: FAIL because labels are not present yet.

**Step 3: Implement labels**

In `app.js`, add human-readable display values and detail-summary copy for:

- `eval_profile: agent_tool_use`
- `verdict_scope: tool_use_schema_triage`
- `agent_tool_use_score`

Keep existing full-adaptive UI behavior unchanged.

**Step 4: Run frontend tests to verify GREEN**

Run:

```powershell
python -m unittest provider_verify_site.tests.test_quick_screen_frontend -v
node --check provider_verify_site/app.js
```

Expected: PASS.

### Task 7: Run full verification

**Files:**
- All changed files

**Step 1: Run full tests and static checks**

Run:

```powershell
python -m unittest discover -s provider_verify_site/tests -v
python -m py_compile provider_verify_site/scripts/task_pack_v1.py provider_verify_site/scripts/local_eval_server.py provider_verify_site/scripts/decision_aggregator_v1.py provider_verify_site/scripts/scorers_v1.py provider_verify_site/scripts/provider_runner.py provider_verify_site/scripts/build_site_data.py provider_verify_site/scripts/auto_eval_storage.py
node --check provider_verify_site/app.js
python -m json.tool provider_verify_site/data/schema_v1.json
python -m json.tool provider_verify_site/eval_tasks/manifest_v1.json
git diff --check
```

Expected:

- all tests pass
- Python compile passes
- JS syntax passes
- JSON parses
- diff check has no whitespace errors

**Step 2: Inspect changed files**

Run:

```powershell
git status --short --branch
git diff --stat
```

Expected: only the design doc, implementation plan, and intended product/test
files are changed.

### Task 8: Local checkpoint only after approval

**Files:**
- All changed files

**Step 1: Ask before commit**

A local commit is a Git state change. Ask for approval before staging and
committing unless the current approval envelope has explicitly expanded to
local commit.

**Step 2: Do not push by default**

Push, tag, release, or PR creation remain out of scope unless separately
approved.
