# Factuality Calibration v1 Design

Date: 2026-07-09
Project: provider_evaluate
Status: approved direction, local implementation design

## 中文评审版

本设计建议新增 `factuality_calibration_v1` 评测 lane，用来判断候选模型是否能“只根据给定材料回答短事实问题”，以及“材料里没有答案时是否能拒答”。它解决的是事实性和幻觉风险：模型不能因为自己知道一些外部信息，就把材料里没有的内容编成答案。

第一版采用平衡方案，共 8 题：

- 4 题材料内有答案，检查模型能否答准并引用原文证据。
- 4 题材料内无答案，检查模型是否返回 `NOT_IN_CONTEXT`，而不是凭经验或外部知识补答案。

这个 lane 只输出独立的 `factuality_score`，不输出 `capability_score`。业务含义是：它只能证明“短事实闭卷材料题表现如何”，不能证明完整通用能力、开放世界知识、实时事实、provider 身份、长上下文能力或生产可用性。

评审时重点看三点：

1. 是否同意第一版用 8 题平衡方案，而不是更小 MVP 或更大的校准套件。
2. 是否同意使用严格输出格式：`ANSWER` 和 `EVIDENCE`。
3. 是否同意分数边界：`factuality_score` 必须独立展示，不能并入 `capability_score`。

本设计不授权代码实现、真实 provider/API 调用、push、发布、生产动作或读取任何密钥。

## Goal

Add a scoped `factuality_calibration_v1` lane to the local provider evaluation
product.

The first version should test whether a model can answer short factual
questions from supplied evidence, and whether it can abstain when the supplied
evidence does not contain the answer. The result must be reported as a scoped
`factuality_score`, not as a general `capability_score`.

## Product Decision

Implement the balanced first version: 8 tasks total.

- 4 answerable closed-context tasks.
- 4 unanswerable-in-context tasks.

This balances usefulness and hallucination resistance. A passing result means
the route can follow supplied evidence in short factual tasks and can avoid
making unsupported claims when the evidence is insufficient. It does not prove
open-world factual knowledge, freshness, provider identity, or production
suitability.

## Method References

Use public benchmarks as methodology references only. Do not copy public
benchmark questions into the local scoring bank.

References used for this design:

- OpenAI SimpleQA:
  <https://openai.com/index/introducing-simpleqa/>
- SimpleQA paper:
  <https://arxiv.org/html/2411.04368v1>
- OpenAI simple-evals SimpleQA implementation:
  <https://github.com/openai/simple-evals/blob/main/simpleqa_eval.py>
- TruthfulQA:
  <https://aclanthology.org/2022.acl-long.229/>
- HELM:
  <https://crfm.stanford.edu/helm/>

Project-specific implications:

- From SimpleQA: keep prompts short, factual, and easy to grade.
- From SimpleQA grading: separate correct, incorrect, and not-attempted style
  outcomes.
- From TruthfulQA: penalize confident unsupported falsehoods, not only missing
  answers.
- From HELM: report score scope, coverage, and what the run does not prove.

## Scope

In scope:

- Add `factuality_calibration_v1` as an evaluation mode.
- Add one balanced task pack with 8 closed-context tasks.
- Require a strict two-field output contract:
  - `ANSWER`
  - `EVIDENCE`
- Score responses deterministically with exact answer and evidence checks.
- Report the result as `factuality_score`.
- Preserve raw model output before judging through the existing run pipeline.
- Keep `capability_score`, `screen_score`, `coding_axis_score`, and
  `agent_tool_use_score` separate.

Out of scope:

- No open-world factuality benchmark.
- No live web search.
- No public benchmark question copying.
- No LLM-as-judge in the first version.
- No confidence calibration, repeated sampling, or temperature sweep in the
  first version.
- No provider identity or route-stability conclusion.
- No long-form factuality claim.
- No production readiness claim.

## Task Shape

Each task gives the model a short evidence passage and one question.

Candidate response contract:

```text
ANSWER: <short answer or NOT_IN_CONTEXT>
EVIDENCE: <exact supporting phrase or NOT_IN_CONTEXT>
```

Answerable tasks:

- The passage contains the answer.
- `ANSWER` must match the expected answer or an allowed alias.
- `EVIDENCE` must be an exact phrase from the passage that supports the answer.

Unanswerable-in-context tasks:

- The passage does not contain the requested answer.
- `ANSWER` must be `NOT_IN_CONTEXT`.
- `EVIDENCE` must be `NOT_IN_CONTEXT`.

Task design constraints:

- Keep passages short enough for deterministic inspection.
- Avoid unstable real-world facts.
- Avoid public benchmark items.
- Include several answer types, such as names, dates, locations, counts, and
  status labels.
- Include unanswerable questions that are plausible but not stated in the
  evidence, so models must not infer from prior knowledge.

## Scoring

Add a deterministic scorer, tentatively `score_closed_context_factuality`.

Suggested 20-point rubric:

- 20 points: correct answer and correct supporting evidence.
- 16 points: correct answer but evidence is missing, too broad, or not an exact
  supporting phrase.
- 10 points: correct abstention intent but minor output-format issue.
- 0 points: answer is wrong for answerable task.
- 0 points: model fabricates an answer for an unanswerable-in-context task.
- 0 points: model uses or claims external knowledge, browsing, or real-time
  data as support.

Hard reject is not necessary by default unless the answer crosses an existing
safety boundary, such as requesting secrets or claiming live execution.

Evidence flags should distinguish failure causes:

- `missing_answer`
- `missing_evidence`
- `wrong_answer`
- `wrong_abstention`
- `unsupported_claim`
- `external_knowledge_claim`
- `invalid_format`

## Report Semantics

For `factuality_calibration_v1`, report fields should be:

- `eval_profile`: `factuality_calibration`
- `verdict_scope`: `factuality_calibration`
- `factuality_score`: integer score on a 0-100 scale
- `capability_score`: `null`
- `capability_tier`: `TIER_UNKNOWN`
- `screen_score`: `null`
- `coding_axis_score`: `null`
- `agent_tool_use_score`: `null`

Coverage map:

- `factuality`: `standard`
- normal screen/coding/tool/context/identity fields remain `not_tested`

Not proven:

- full general capability
- open-world factual knowledge
- factual freshness beyond supplied evidence
- long-form factuality
- provider identity
- long-context capability
- multi-session route stability
- production suitability

## Integration Points

Expected files:

- `provider_verify_site/scripts/task_pack_v1.py`
  - add prompt constants and `get_factuality_calibration_task_pack()`
- `provider_verify_site/scripts/scorers_v1.py`
  - add `score_closed_context_factuality()`
- `provider_verify_site/scripts/decision_aggregator_v1.py`
  - include factuality task IDs in score groups
- `provider_verify_site/scripts/local_eval_server.py`
  - add `FACTUALITY_CALIBRATION_EVAL_MODE`
  - add scorer dispatch
  - route eval mode to the factuality task pack
  - add profile and score-scope fields
- `provider_verify_site/scripts/build_site_data.py`
  - infer factuality profile and score scope
  - preserve `factuality_score`
  - keep scoped profiles out of `capability_score`
- `provider_verify_site/data/schema_v1.json`
  - allow `factuality_calibration` profile and `factuality_score`
- `provider_verify_site/eval_tasks/manifest_v1.json`
  - mark `factuality_calibration_v1` implemented initial
  - register the implemented task IDs
- `provider_verify_site/app.js`
  - display factuality scope labels and score fields
- tests under `provider_verify_site/tests/`

## Testing Strategy

Use test-driven implementation.

Initial test slices:

1. Task pack returns 8 `closed_context_factuality` tasks.
2. Task pack includes 4 answerable and 4 unanswerable tasks.
3. Scorer passes exact answer plus exact evidence.
4. Scorer gives partial credit for correct answer with weak evidence.
5. Scorer passes correct `NOT_IN_CONTEXT` abstention.
6. Scorer fails hallucinated answer when the task is unanswerable.
7. Scorer fails external-knowledge claims.
8. Local payload validation accepts `factuality_calibration_v1`.
9. Local run creation records `eval_profile`, `verdict_scope`, and
   `factuality_score`.
10. Run readback and run-list responses include `factuality_score`.
11. Site-data build treats the result as scoped evidence and keeps
    `capability_score` null.
12. Schema and frontend expose the field without presenting it as a full model
    capability score.

## Operating Boundaries

This feature is safe to implement locally because it adds prompts,
deterministic scoring, schema/report fields, and UI interpretation only.

It must not add any new real external call path. Running the evaluation against
a provider still depends on the existing user-triggered provider request path.
No live provider/API run, push, release, tag, or production action is part of
this design by default.
