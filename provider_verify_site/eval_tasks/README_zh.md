# 本地题库说明

状态：v1 题库骨架，运行器尚未完全迁移。

这个目录用于登记 provider 能力评估题目。它的目标不是复制公开榜单，而是维护一套本地、可重复、可自动评分的题库。

## 设计原则

1. 公开 benchmark 只做方法参考，不直接作为正式评分题库。
2. 候选模型看到的是 `public_prompt`，看不到答案、隐藏断言和 holdout 题。
3. 每道题必须有 `task_id`、`version`、`axis`、`profile`、`scorer` 和 `score_scope`。
4. 原始请求和原始输出必须先保存，再评分。
5. coding 题必须用可执行检查，不能只看回答是否像代码。
6. provider identity、routing risk、latency、cost 不进入能力轴分数，只作为推荐上限或运营指标。
7. 当前阶段不包含多模态。

## 当前题库来源

当前自动评测任务仍由代码内置在：

```text
provider_verify_site/scripts/task_pack_v1.py
```

本目录的 `manifest_v1.json` 先把这些任务登记为版本化题目，后续再逐步迁移到独立 prompt、answer key、hidden check 和生成器文件。

## 题库层级

建议结构：

```text
provider_verify_site/eval_tasks/
  README_zh.md
  manifest_v1.json
  public/
    screen_v2/
    capability_6d_v1/
  private/
    answer_keys/
    holdout/
    generators/
    coding_repos/
  scorers/
```

当前只创建 README 和 manifest。`private/` 未来保存本地答案、隐藏检查、holdout 题和生成器，不保存 API key、token、password 或 provider secret。

## 推荐使用方式

日常 provider 检测：

```text
screen_v2 -> coding_probe_v2（如果要用于 coding）-> holdout（只在 close-call 或高价模型声明时）
```

正式比较：

```text
capability_6d_v1 + coding_probe_v2 + optional long_context_v1 + route risk audit
```

## 分数解释

- `screen_v2` 只输出快速筛查分，不代表完整模型能力。
- `coding_probe_v2` 只输出代码能力轴分，不代表完整模型能力。
- `capability_6d_v1` 才允许输出综合能力分。
- `identity` 和 `context_class` 是独立证据通道，不直接证明模型质量或唯一身份。

详细设计见：

```text
docs/plans/2026-06-29-provider-capability-task-bank-design.md
docs/plans/2026-06-29-general-model-evaluation-scoring-v2.md
docs/rubrics/general-model-evaluation-rubric-v2.md
```
