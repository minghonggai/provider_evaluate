# Opus Red-Team Review Packet

Audience: Opus-class review model
Mode: adversarial review / challenge
Mainline owner: Codex
Risk level: L1
Live actions requested: none

## Objective

Review the `model_evaluate` provider verification and capability evaluation
mechanism as a hostile reviewer.

Your goal is to find ways the current mechanism could:

- falsely pass a weak, cheap, or substituted model;
- falsely reject a strong model because of infrastructure or prompt artifacts;
- overclaim model identity from capability evidence;
- merge separate provider routes into one misleading result;
- let memory, prior outputs, or judge notes contaminate a run;
- let automated scoring be gamed by format mimicry or rubric overfitting.

This is not a model evaluation run. You are reviewing the evaluation system
itself.

## Hard Boundaries

Do not:

- request, read, infer, print, or store API keys, provider secrets, cc-switch
  DB content, environment dumps, or credential files;
- call live provider APIs or run identity probes;
- modify project files;
- inspect source control-plane projects outside `model_evaluate`;
- unblind candidate identities;
- read historical candidate raw outputs unless Codex explicitly provides
  blinded excerpts for a specific review question;
- decide the final provider verdict;
- mark any route as `identity_verified`.

You may:

- read the listed project files;
- inspect schemas, prompts, rubrics, scoring scripts, generated report examples,
  and tests;
- produce adversarial findings and recommended fixes;
- propose extra deterministic checks and review gates.

## Context To Read

Read these files first, if available:

- `AGENTS.md`
- `README.md`
- `docs/status/current_status.md`
- `docs/plans/current_plan.md`
- `docs/findings/current_findings.md`
- `provider_verify_site/README_zh.md`
- `provider_verify_site/opus_4_8_review_packet.md`
- `provider_verify_site/data/schema_v1.json`
- `provider_verify_site/data/report_index.json`
- `provider_verify_site/scripts/task_pack_v1.py`
- `provider_verify_site/scripts/scorers_v1.py`
- `provider_verify_site/scripts/decision_aggregator_v1.py`
- `provider_verify_site/scripts/auto_eval_storage.py`
- `provider_verify_site/scripts/build_site_data.py`
- `provider_verify_site/scripts/local_eval_server.py`
- `provider_verify_site/scripts/provider_runner.py`
- `provider_verify_site/tests/test_scorers_v1.py`
- `provider_verify_site/tests/test_decision_aggregator_v1.py`
- `provider_verify_site/tests/test_build_site_data.py`
- `provider_verify_site/tests/test_local_eval_server.py`

If a file is missing, record it as unavailable and continue.

## Review Lanes

### 1. Capability vs Identity Boundary

Attack the boundary between:

- `capability_status`
- `identity_status`
- `quality_status`
- `code_quality_status`
- `can_use_for_coding`
- `routing_risk`

Look for labels, UI text, scoring rules, or summaries that could make a user
believe "strong output" means "true upstream Opus".

### 2. Provider Route Isolation

Check whether separate routes can be accidentally merged.

Attack:

- `route_key` design;
- `provider_protocol`;
- `base_url_host`;
- missing host fallback such as `unknown-host`;
- model aliases that point to different upstreams;
- provider slots with the same advertised model but different backend behavior;
- retry runs that may use a different provider route.

### 3. Automated Scoring Robustness

Attack deterministic and judge-like scoring.

Look for:

- format-only answers that score too high;
- generic safety wording that hides failure to answer;
- coding answers that describe a fix but would fail execution;
- rubric leakage or prompt overfitting;
- LLM judge preference leakage;
- missing anchor sets for judge drift;
- single-run overconfidence.

### 4. Coding Evaluation

Challenge whether coding ability is truly tested.

Look for:

- single-file toy bugs that a weak model can memorize;
- tests that reward explanation more than executable correction;
- missing multi-file reasoning;
- missing regression tests;
- missing command execution evidence;
- no distinction between code review, patch generation, and runtime operation.

### 5. Context And Memory Contamination

Attack contamination controls.

Look for:

- candidate sessions that read the whole project before answering;
- hidden memory or prior conversation carryover;
- second candidate seeing the first candidate's output;
- judge notes leaking into future candidate prompts;
- project files that reveal expected answers;
- run records that mix raw output and scoring too early.

### 6. Provider Proxy And Middleware Attacks

Assume an opaque provider may:

- rewrite `model` fields;
- rewrite message IDs;
- omit upstream request IDs;
- silently truncate context;
- fallback between models;
- fake headers;
- accept Anthropic-shaped parameters but return non-native structures;
- route differently by account, time, prompt size, or balance.

Identify which of these attacks the current mechanism catches, which it misses,
and which it should mark only as `route_unverified` or `infra_inconclusive`.

### 7. UI And Product Semantics

Review whether dashboard labels are safe for a human operator.

Look for words like "verified", "confirmed", "Opus", "pass", "safe", or
"approved" that may be too strong for the evidence.

### 8. Evidence Preservation

Check whether raw evidence is preserved before scoring.

Look for:

- raw output mutation;
- overwrite behavior;
- missing request metadata;
- missing failure class;
- missing timestamp or route fields;
- generated report fields that cannot be traced back to source evidence.

## Required Output Format

Reply in Chinese unless quoting code or field names.

Use this exact structure:

````markdown
# Red-Team Review Result

## Accepted Facts

- ...

## Executive Verdict

verdict: accept | revise_before_use | block
confidence: high | medium | low
reason: ...

## P0 Findings

| ID | Finding | Exploit Path | Impact | Recommended Fix |
| --- | --- | --- | --- | --- |

## P1 Findings

| ID | Finding | Exploit Path | Impact | Recommended Fix |
| --- | --- | --- | --- | --- |

## P2 Findings

| ID | Finding | Exploit Path | Impact | Recommended Fix |
| --- | --- | --- | --- | --- |

## False-Pass Paths

- ...

## False-Reject Paths

- ...

## Provider Bypass Paths

- ...

## Coding Evaluation Weak Spots

- ...

## Scoring And Judge Drift Risks

- ...

## Recommended Deterministic Gates

- gate_name:
  - catches:
  - implementation hint:
  - false-positive risk:

## Tests To Add

- ...

## Boundary Check

- secrets accessed: no
- live APIs called: no
- source control-plane project touched: no
- candidate identities unblinded: no
- final provider verdict assigned: no
````

## Quality Bar

A useful review should be adversarial but bounded.

Good findings name:

- the exact file, field, prompt, or workflow being attacked;
- the concrete exploit path;
- the user-visible business risk;
- a minimal fix or test.

Weak findings are:

- generic complaints without a reproduction path;
- claims that require secret access;
- identity conclusions based only on self-report;
- recommendations that turn the lightweight lane into a heavy formal benchmark.
