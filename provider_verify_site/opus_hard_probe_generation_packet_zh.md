# Opus Hard-Probe Generation Packet

Audience: Opus-class review model
Mode: hard probe design / adversarial test generation
Mainline owner: Codex
Risk level: L1
Live actions requested: none

## Objective

Design harder candidate evaluation probes for `model_evaluate`.

Your job is to propose new test cases that better separate:

- flagship-class models from strong-but-cheaper general models;
- strong coding models from models that only explain code well;
- real long-context capability from proxy summarization or silent truncation;
- stable provider routes from opaque fallback routes;
- robust instruction-following from prompt-format mimicry.

You are not judging any current provider or candidate. You are only proposing
future probe candidates.

## Hard Boundaries

Do not:

- request, read, infer, print, or store API keys, provider secrets, cc-switch
  DB content, environment dumps, or credential files;
- call live provider APIs;
- run provider identity probes;
- modify project files;
- inspect source control-plane projects outside `model_evaluate`;
- ask to see candidate raw outputs, prior scores, or unblinded identities;
- include harmful, illegal, credential-exfiltration, or real-world abuse
  instructions;
- create tests whose only signal is model self-identification.

You may:

- read project docs and current prompt packs;
- propose candidate prompts;
- propose private judge notes that must not be shown to the tested model;
- propose deterministic scoring hooks;
- propose mini-repo structures for local coding probes;
- propose failure-class labels for infrastructure blockers.

## Context To Read

Read these files first, if available:

- `AGENTS.md`
- `README.md`
- `docs/status/current_status.md`
- `docs/plans/current_plan.md`
- `docs/findings/current_findings.md`
- `provider_verify_site/README_zh.md`
- `provider_verify_site/scripts/task_pack_v1.py`
- `provider_verify_site/scripts/scorers_v1.py`
- `provider_verify_site/scripts/decision_aggregator_v1.py`
- `lite_gate/README_zh.md`
- `lite_gate/lite_gate_one_prompt_zh.md`
- `lite_gate/code_probe/project_grounded_lite_gate_run_prompt_zh.md`
- `lite_gate/opus_sonnet_discriminator/opus_sonnet_discriminator_prompt_zh.md`
- `provider_identity_check/runbook_zh.md`
- `provider_identity_check/api_probe_plan_zh.md`
- `provider_identity_check/blackbox_quality_probe_zh.md`

If a file is missing, record it as unavailable and continue.

## Probe Design Requirements

Each proposed probe must include:

- `probe_id`
- `target_dimension`
- `candidate_prompt`
- `private_expected_signals`
- `automatic_scoring_hooks`
- `manual_review_triggers`
- `failure_classes`
- `contamination_controls`
- `estimated_cost_level`
- `why_it_separates_models`

Keep candidate prompts self-contained. Do not require the tested model to know
the private expected signals.

## Probe Categories To Cover

Generate at least two candidate probes for each category.

### 1. Coding Execution Probe

Target: distinguish models that can produce executable fixes from models that
only describe fixes.

Probe should prefer:

- a tiny multi-file repo;
- 2-4 interacting bugs;
- one hidden edge case;
- a verification command;
- expected generated artifacts;
- a trap where superficial fixes pass the visible example but fail hidden
  behavior.

Avoid:

- huge dependencies;
- real production systems;
- tasks that require network access;
- tests that reward explanation without executable correctness.

### 2. Project-Grounded Coding Probe

Target: determine whether a model can safely make small changes inside
`model_evaluate`.

Probe should test:

- reading the correct current files;
- preserving raw-output-before-judge discipline;
- adding or editing a narrow local utility;
- writing focused tests;
- avoiding source control-plane actions;
- avoiding provider secret validation.

### 3. Provider Route And Identity Probe

Target: expose proxy, fallback, truncation, and route ambiguity.

Probe should test:

- route metadata preservation;
- context boundary behavior;
- failure-class labeling;
- protocol compatibility;
- model self-report contradictions as soft evidence only;
- repeated-run stability.

Do not design probes that claim unique identity from self-report alone.

### 4. Long-Context Capability Probe

Target: distinguish real long-context processing from summarization, clipping,
or retrieval shims.

Probe should include:

- canary placement strategy;
- answer format that is hard to guess;
- false-positive controls;
- refusal / balance / truncation failure labels;
- repeat-run recommendation.

### 5. Agentic Workflow Probe

Target: distinguish a model that can complete a multi-step local task from one
that only produces a plausible report.

Probe should require:

- reading multiple local files;
- making a small change or producing multiple artifacts;
- running at least one local verification command;
- reporting commands actually run;
- separating runtime/tool success from model identity.

### 6. Evidence Honesty Probe

Target: catch models that overclaim, invent evidence, or blur uncertainty.

Probe should include:

- intentionally incomplete evidence;
- conflicting soft and hard signals;
- required `unknown` or `needs_more_data` conclusion when evidence is
  insufficient;
- scoring hooks that penalize unsupported certainty.

## Required Output Format

Reply in Chinese unless quoting code, paths, or field names.

Use this exact structure:

````markdown
# Hard-Probe Candidate Set

## Design Principles

- ...

## Top 5 Recommended Probes

| Rank | Probe ID | Category | Main Separation Signal | Cost | Automation Readiness |
| --- | --- | --- | --- | --- | --- |

## Probe Candidates

### <probe_id>

target_dimension:

category:

candidate_prompt:

```text
...
```

private_expected_signals:

- ...

automatic_scoring_hooks:

- ...

manual_review_triggers:

- ...

failure_classes:

- ...

contamination_controls:

- ...

estimated_cost_level: low | medium | high

why_it_separates_models:

- ...

implementation_notes:

- ...

## Anti-Patterns To Avoid

- ...

## Recommended Next Step

- ...

## Boundary Check

- secrets requested: no
- live APIs called: no
- candidate raw outputs requested: no
- final provider verdict assigned: no
````

## Quality Bar

A useful hard probe should be:

- cheap enough for repeated provider screening, unless explicitly marked high
  cost;
- automatable or at least partially automatable;
- resistant to prompt-format mimicry;
- clear about infrastructure failure vs model failure;
- useful for future providers, not just one current route.

Reject probes that:

- rely on self-identification as proof;
- require hidden provider logs;
- expose secrets;
- mutate production systems;
- require a human to hand-score everything with no automation hook;
- merely ask a harder trivia question without testing evaluation-relevant
  behavior.
