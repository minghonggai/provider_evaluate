# Model Capability Report 本地评测站点

这个站点是 `model_evaluate` 的本地模型能力评测看板。它的目标不是“绝对证明 provider 的唯一上游身份”，而是快速判断：

- 候选模型是不是明显弱模型或廉价模型。
- coding 和项目工作能力是否达到可试用水平。
- 是否值得进入小范围试用、补测，还是直接淘汰。
- provider 身份和路由风险是否会限制使用范围。

界面支持中文 / English 双语切换，默认中文。顶部 `中文 / English`
按钮只改变界面文案，不改变评测任务、评分逻辑、run record 或 provider
请求字段。

## 核心边界

- API key 只在浏览器提交到本机 `127.0.0.1:8766` 的一次请求中使用，不写入 run record、report index 或日志字段。
- 本站点会通过本地 API 服务调用你填写的 provider endpoint 来完成 5 题 quick screen。
- 不读取 cc-switch DB/config，不自动抓取系统环境变量，不保存 provider secret。
- 不执行 DingTalk、RB1 cutover、生产凭证检查、workbench dispatch 或任何生产动作。
- 不把 `capability pass` 自动解释成 `identity_verified`。
- 原始模型输出先保存，再评分和汇总。

## 启动方式

在项目根目录运行两个本地服务。

静态站点：

```powershell
python -m http.server 8765 --bind 127.0.0.1 -d provider_verify_site
```

本地评测 API：

```powershell
python provider_verify_site/scripts/local_eval_server.py --root . --host 127.0.0.1 --port 8766
```

打开页面：

```text
http://127.0.0.1:8765/
```

健康检查：

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8766/health
```

## 一次 quick screen 怎么填

在页面的 `Quick Screen Run` 区域填写：

- `Provider Alias`：你给这条 provider 路由起的名字，例如 `opus_4_8syapi`。
- `Base URL`：provider 的 OpenAI-compatible 或 Anthropic-compatible endpoint。
- `API Key`：本次会话使用的 key。页面不会保存它。
- 点击 `读取供应商模型列表`：本地后台会用 Base URL 和本次 key 读取 provider 自己返回的模型列表，并自动识别协议。
- `Model Name` / `待评测模型`：从 provider 返回的模型列表里选择，例如 `claude-opus-4-8`；这个名字不是手动输入的。
- `Claimed Model`：可选的人类可读声明，例如 `Claude Opus 4.8`。留空时会默认用所选模型名记录对照。
- `Mode`：
  - `Quick Screen`：默认 5 题快速筛选，用来判断是不是明显弱模型。
  - `Coding Probe`：项目级 coding 小测，只在候选模型可能用于代码工作时运行。

页面会根据 `Mode` 自动切换标题、说明和提交按钮文案：选择
`Quick Screen` 时按钮显示 `Start Quick Screen`；选择 `Coding Probe`
时按钮显示 `Start Coding Probe`。

## 默认完整评估怎么跑

当前页面默认使用完整评估入口。你只需要填写 provider 信息、读取模型列表、选择待测模型，然后点击开始完整评估。

完整评估不是把前一阶段模型输出喂给后一阶段模型。每一阶段都是独立 prompt、独立 provider 请求、独立 run 目录；前一阶段结果只在本地前端用来决定是否继续追加下一阶段。

运行顺序是：

```text
1. screen_v2 能力筛查
2. holdout_screen_v1 临界复测（只在 close-call 时自动触发）
3. coding_probe_v1 代码探针（筛查/复测仍可用时自动触发）
```

`holdout_screen_v1` 的触发条件是：

- `screen_score` 在 65-84 之间；
- 或 `decision_v2 = LIMITED_USE`；
- 并且没有 hard reject、provider 请求失败、`INCONCLUSIVE` 或 `RERUN_REQUIRED`。

如果初筛很强，系统会跳过 holdout，直接进入 coding probe。
如果初筛或 holdout 不可用，系统会停止，不再花费额外请求跑 coding probe。

报告解释口径：

- `screen_v2` 和 `holdout_screen_v1` 都只产生 `screen_score`，不产生正式 `capability_score`。
- holdout 报告会标记 `verdict_scope = holdout_screen_triage` 和 `score_basis.holdout_checked = true`。
- coding probe 只证明当前项目级 coding 小任务表现，不代表完整通用能力。
- 即使三段都通过，也仍然不能证明 provider 真实上游模型身份；身份和路由稳定性要另跑专门检查。

选择 `Quick Screen` 并点击 `Start Quick Screen` 后，本地 API 会自动执行 5 个固定任务：

1. 边界安全：是否会要求你提供 secret，是否会建议生产动作。
2. 指令遵循：是否按指定结构输出。
3. 证据诚实：是否伪造“我已经运行/验证过”。
4. coding 修复：是否能给出可执行、可测试的修复代码。
5. 产品化表达：是否能给业务用户清楚说明结论、选项和下一步。

选择 `Coding Probe` 并点击 `Start Coding Probe` 时，本地 API 会执行 1 个项目级 coding 任务：

- 要求模型返回一个可执行 Python 工具，用于创建 Lite Gate run record。
- 自动 verifier 会在临时目录里运行候选代码，检查目录创建、文件命名、metadata、raw-before-judge、pack 映射、默认不覆盖和不触碰 secret。
- 这比默认 quick screen 里的单函数修复更贴近当前项目工作。
- 它仍然是能力证据，不是 provider 身份验证。

## 自动生成什么文件

每次运行会在本地生成：

```text
auto_eval_runs/YYYY-MM-DD/<run_id>/
  run_manifest.json
  run_report.json
  <task_id>_request.json
  <task_id>_response.json
  <task_id>_response.txt
  <task_id>_score.json
```

同时会刷新：

```text
provider_verify_site/data/report_index.json
```

`request.json` 会把 `base_url` 记为 `masked`，不会保存 API key。

## 结果怎么解释

看板会把结论分开显示：

- `capability_score`：能力总分，100 分制。
- `capability_tier`：能力档位，可能是 `TIER_STRONG`、`TIER_USABLE`、`TIER_WEAK` 等。
- `coding_score`：coding 题归一化分数。
- `code_quality_status`：coding 质量标签。
- `recommended_use`：推荐用途，例如 `coding_trial`、`needs_more_data`、`do_not_use`。
- `identity_status`：身份状态。默认自动评测只能给 `route_unverified`，不能直接给 `identity_verified`。
- `routing_risk`：路由风险。旧记录或缺少 base URL 的记录会被标成 `high`。
- `route_comparable`：这条 route 是否可以和其他 provider route 做可靠比较。
- `identity_confidence`、`capability_confidence`、`route_stability_confidence`：分别表示身份、能力、路由稳定性的证据强度。

关键解释：

- `CONTINUE_TRIAL` 表示可以进入受控试用，不表示真实身份已验证。
- `INCONCLUSIVE` 表示证据不足，常见原因是超时、额度、网络或接口 contract 问题，不等于模型弱。
- `REJECT_WEAK` 表示当前 5 题 quick screen 下不建议继续投入。
- 500K/1M 长上下文通过只能说明能力档位，不单独证明唯一上游模型身份。

## 已加固的机制

当前版本已经补强：

- route fingerprint：用 `route_fingerprint` 和 `base_url_host_hash` 区分 provider route，避免不同 route 混在一起。
- unknown-host 防误判：缺少 host 的旧记录会被标为 `route_comparable=false` 和 `routing_risk=high`。
- 严格枚举：核心状态字段有 schema enum，减少把能力标签写进身份字段的风险。
- 失败分类：超时、额度/鉴权、网络、contract invalid 会分开记录。
- 评分抗伪装：只写标题和空泛内容不会再拿高分。
- 证据边界：把“能力强”“身份已验证”“路由稳定”拆成不同字段。

## 手动刷新报告

如果你只是改了已有记录，想重新生成看板数据：

```powershell
python provider_verify_site/scripts/build_site_data.py --root . --output provider_verify_site/data/report_index.json
```

## 验证命令

```powershell
python -m unittest discover -s provider_verify_site/tests -v
node --check provider_verify_site/app.js
python provider_verify_site/scripts/build_site_data.py --root . --output provider_verify_site/data/report_index.json
```

## 推荐使用方式

新 provider 先跑 `Quick Screen`。只有在 `capability_tier` 和 `coding_score` 都接近参考高分模型时，再跑 `Coding Probe`。如果 coding probe 也通过，再考虑更贵的 context probe、identity probe 或 Mini AB。

这套机制适合“快速筛掉明显不行的 provider”和“找出值得继续测的候选 route”。它不是 provider 法证系统，不能替代官方上游凭证、透明 request id、provider 管理后台配置或可信渠道合同。
