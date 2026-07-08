# Provider Capability Benchmark v2

## 1. 目标

这套方法用于本地快速评估某个 provider 实际提供的模型能力是否达到可用标准，并帮助判断：

- 该 route 是否明显弱于宣称水平
- 该模型是否适合一般对话、结构化工作流、代码任务
- 是否需要进一步做 identity / route stability / long-context 验证

它不是官方身份认证系统，也不直接证明上游一定是某个官方模型。

## 2. 当前 v1 的定位

当前项目中的 `screen_v2` / `quick_screen_v1` 更适合作为：

- 本地 provider 快速筛查
- 排除明显弱模型、降级 route、格式不稳定 route
- 在投入更多成本之前做第一层体检

当前题库的优点：

- 可自动运行
- 可自动判分
- 保留 raw output
- coding 题已带 verifier，避免只看“像不像代码”

当前题库的不足：

1. 一部分题测到的是“格式服从度”或“工作流配合度”，不完全等于核心模型能力
2. 题量偏少，容易被固定 prompt 适配
3. 缺少轮换题和隐藏题，抗污染能力不够
4. 总分没有显式不确定性，容易给人“一次跑完就定档”的错觉
5. route identity / long context / capability 三类证据虽然概念上分开了，但方法文档还不够完整

## 3. 外部方法参考

本项目建议借方法，不直接照搬公开题卷。

### 3.1 LLM Stats

可借的部分：

- 把总分和分项分拆开
- 明确区分 verified 与 self-reported
- 用保守分或不确定性约束总分，而不是只看单次结果

不建议直接照搬的部分：

- 它本质是 benchmark 聚合与 rating 系统，不是单个 provider endpoint 的本地验真工具

参考：

- <https://llm-stats.com/methodology>
- <https://llm-stats.com/methodology/llm-stats-score>

### 3.2 LiveBench

可借的部分：

- 题目定期更新
- 目标是降低 test contamination
- 尽量使用客观 ground truth 自动判分

对本项目的启发：

- 我们不应长期只用一套固定题
- 应引入轮换题和隐藏 holdout 题

参考：

- <https://livebench.ai/>

### 3.3 LiveCodeBench

可借的部分：

- coding 不只测 code generation
- 还测 self-repair、execution、test-output reasoning
- 用时间更新来降低代码题污染

对本项目的启发：

- coding 维度至少要拆成“单函数修复”和“项目级小仓库修复”

参考：

- <https://livecodebench.github.io/>

### 3.4 SWE-Bench Verified

可借的部分：

- 任务更接近真实工程问题
- 强调实例可解、描述清晰、测试可靠

对本项目的启发：

- 高价值 coding probe 不应只停留在玩具函数
- 应至少有一类“多文件 + 测试驱动”的迷你仓库任务

参考：

- <https://www.swebench.com/verified>
- <https://openai.com/index/introducing-swe-bench-verified/>

### 3.5 IFEval

可借的部分：

- instruction following 用“可验证约束”自动判分
- 避免纯主观评价

对本项目的启发：

- 指令遵循题应尽量程序化验证，而不是只看输出长得像不像模板

参考：

- <https://github.com/google-research/google-research/tree/master/instruction_following_eval>
- <https://arxiv.org/abs/2311.07911>

### 3.6 Arena-Hard

可借的部分：

- 用 agreement to human preference 和 separability 评价 benchmark 自身质量
- 强调 freshness、confidence interval、区分度

对本项目的启发：

- 我们设计题库时，不只是问“题难不难”
- 还要问“能不能把强弱模型稳定拉开”

参考：

- <https://www.lmsys.org/blog/2024-04-19-arena-hard/>

## 4. v2 的设计原则

### 4.1 能力、身份、路由分轨

必须拆成三条证据通道：

1. `capability`
   - 模型实际答题、推理、coding、结构化执行能力
2. `identity`
   - 是否有证据支持它确实是宣称的官方模型
3. `route_stability`
   - 同一个 provider / model route 是否稳定、是否疑似波动或降级

规则：

- capability 强，不等于 identity 已验证
- identity 可疑，不等于 capability 一定弱
- route 不稳，即使能力强，也不应直接推荐为主生产通道

### 4.2 客观判分优先

优先级：

1. 程序可验证
2. 规则可验证
3. 必要时才用 LLM-as-judge

能用 verifier / parser / unit tests 的地方，不应先用主观 judge。

### 4.3 公开题 + 隐藏题 + 轮换题

每个核心能力轴建议同时包含：

- `public_seed`
  - 长期保留的公开基础题，用于稳定复现
- `private_holdout`
  - 不向候选模型暴露答案规则的隐藏题
- `rotating_fresh`
  - 周期轮换的新题，降低污染

建议占比：

- public_seed: 30%
- private_holdout: 40%
- rotating_fresh: 30%

### 4.4 分数带置信度

v2 不建议只给单一绝对分。

建议最少展示：

- 总分
- 分项分
- 任务通过数
- 样本量
- 置信标签：`low` / `medium` / `high`

如果未来题量扩大，可升级为：

- bootstrap 置信区间
- 保守分 `mean - penalty`

## 5. v2 推荐能力结构

建议把“核心能力分”和“工作流兼容分”拆开。

### 5.1 核心能力分 Core Capability

总分建议 100 分，分成 5 轴：

1. `instruction_verifiable` - 20 分
   - 参考 IFEval
   - 检查多约束是否真正满足
   - 尽量不用纯格式模板题

2. `reasoning_and_data` - 20 分
   - 表格统计
   - 条件推导
   - 简短多步骤推理
   - 结果必须有唯一正确答案

3. `coding_bugfix` - 20 分
   - 单函数修复
   - 带 verifier
   - 检查是否一次修对、是否引入回归

4. `coding_project` - 20 分
   - 小型多文件仓库
   - 运行测试
   - 更接近 SWE-Bench / LiveCodeBench 风格

5. `honesty_and_boundary` - 20 分
   - 不能伪造已执行
   - 不能把 capability 说成 identity proof
   - 不能请求 secret / 生产凭证

### 5.2 工作流兼容分 Workflow Compatibility

单独展示，不并入核心能力总分：

- 是否稳定按结构输出
- 是否容易误拒答
- 是否能生成适合 operator 使用的结果
- 是否容易出现乱码、格式漂移、漏字段

原因：

- 某个模型可能很聪明，但不适合严格结构化工作流
- 某个 route 可能能力不错，但在固定 prompt 框架下表现不稳定

### 5.3 Route Risk Card

单独展示：

- identity_status
- route_stability_status
- long_context_status
- transparency_status

这张卡是“能不能放心接入”的证据，不是“模型智力分”。

## 6. 对现有 v1 题目的处理建议

### 6.1 保留并升级

1. `coding_fix`
   - 保留
   - 从单题扩成 3 道不同 bug pattern
   - 增加隐藏变体

2. `data_table_analysis`
   - 保留
   - 扩成多组表格推理题
   - 避免总是同一张表

3. `evidence_honesty`
   - 保留
   - 继续作为硬边界题

### 6.2 降权或迁移

1. `instruction_following`
   - 不删除
   - 从“固定模板输出”改成“可验证约束完成度”

2. `product_communication`
   - 不应作为核心能力主轴
   - 应迁移到 workflow compatibility

3. `reasoning_planning`
   - 保留思路
   - 但不要只用关键词顺序判分
   - 需要更稳的结构化判别逻辑

### 6.3 新增

1. `mini_repo_fix`
   - 多文件代码修复
   - 必须运行测试

2. `fresh_holdout_reasoning`
   - 月度轮换推理题

3. `verifiable_instruction_bundle`
   - 参考 IFEval 风格的约束组合题

4. `self_repair_coding`
   - 首次失败后给错误信息，看是否二次修正

## 7. 评分与决策建议

### 7.1 推荐展示

前台不要只显示一个分数，建议至少显示：

- `Core Capability`
- `Coding`
- `Workflow Compatibility`
- `Risk Flags`
- `Confidence`

### 7.2 推荐决策层

建议输出以下标签，而不是只说高分低分：

- `RECOMMENDED`
- `LIMITED_USE`
- `WORKFLOW_RISK`
- `NOT_RECOMMENDED`
- `IDENTITY_UNVERIFIED`

### 7.3 一票否决项

以下任一命中，可直接把 route 限制在 `LIMITED_USE` 或更低：

- 请求或诱导 secret
- 明显伪造执行记录
- 把 capability probe 直接说成 identity proof
- coding verifier 持续 0 分
- 多轮结果严重不稳定

## 8. MVP 落地顺序

### Phase 1

先把当前系统升级为“更可信的本地能力筛查”：

1. 拆分 `core capability` 和 `workflow compatibility`
2. 新增 `mini_repo_fix`
3. 把 `instruction_following` 改成 verifiable constraints
4. 给每个 task 显示明确评分依据与 evidence flags

### Phase 2

增强抗污染与可信度：

1. 引入 private holdout
2. 引入 rotating fresh tasks
3. 为 close-call route 增加重复采样
4. 给总分加 confidence

### Phase 3

增强对外展示能力：

1. 做版本化 methodology 页面
2. 展示每个 axis 的任务覆盖情况
3. 展示 “what this proves / what this does not prove”

## 9. 当前结论

当前项目已经具备：

- 本地自动跑
- 自动保留 raw output
- 基础自动判分
- coding verifier
- provider route 视角

但它目前更准确的对外表述应是：

> 本地 provider capability screening system

而不是：

> 官方级模型身份认证或完整通用 benchmark

下一步应优先完成：

1. 把核心能力分与工作流兼容分拆开
2. 给 coding 增加项目级 probe
3. 给题库增加隐藏题与轮换题
4. 给总分增加置信说明
