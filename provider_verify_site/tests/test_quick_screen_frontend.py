import unittest
from pathlib import Path


class QuickScreenFrontendTests(unittest.TestCase):
    def test_index_exposes_quick_screen_provider_fields(self):
        html = Path("provider_verify_site/index.html").read_text(encoding="utf-8")

        self.assertIn('id="quickScreenForm"', html)
        self.assertIn('name="provider_alias"', html)
        self.assertIn('name="base_url"', html)
        self.assertIn('name="api_key"', html)
        self.assertIn('name="model_name"', html)
        self.assertIn('id="modelNameSelect"', html)
        self.assertIn('id="loadModelsButton"', html)
        self.assertIn('id="modelNameStatus"', html)
        self.assertIn('name="provider_protocol" value="auto"', html)
        self.assertIn('name="assessment_plan"', html)
        self.assertIn('value="full_adaptive"', html)
        self.assertIn('name="eval_mode"', html)
        self.assertIn('value="screen_v2"', html)
        self.assertIn('data-i18n="models.sourceNotice"', html)
        self.assertNotIn('data-i18n="form.mode"', html)
        self.assertNotIn('data-i18n="form.protocol"', html)
        self.assertNotIn('name="raw_candidate_output"', html)

    def test_app_uses_quick_screen_endpoint(self):
        js = Path("provider_verify_site/app.js").read_text(encoding="utf-8")

        self.assertIn("/api/quick-screen-runs", js)
        self.assertIn("/api/provider-models", js)
        self.assertIn("DEFAULT_LOCAL_EVAL_SERVER", js)
        self.assertIn('window.location.port === "8766"', js)
        self.assertIn("loadProviderModels", js)
        self.assertIn("collectQuickScreenPayload", js)
        self.assertIn("provider_protocol", js)
        self.assertIn("setProviderProtocol", js)
        self.assertIn("providerProtocolLabel", js)
        self.assertIn("body.protocol", js)
        self.assertIn("coding_probe_v1", js)
        self.assertIn("screen_v2", js)
        self.assertIn("holdout_screen_v1", js)
        self.assertIn("loadRunResult", js)
        self.assertIn("loadReport()", js)
        self.assertIn("Identity confidence", js)
        self.assertIn("Capability confidence", js)
        self.assertIn("Route stability", js)
        self.assertIn("screen_score", js)
        self.assertIn("coding_axis_score", js)

    def test_app_supports_one_click_adaptive_full_assessment(self):
        js = Path("provider_verify_site/app.js").read_text(encoding="utf-8")

        self.assertIn("assessment_plan", js)
        self.assertIn("full_adaptive", js)
        self.assertIn("runAdaptiveAssessment", js)
        self.assertIn("runSingleEvalMode", js)
        self.assertIn("shouldRunHoldoutScreen", js)
        self.assertIn("shouldRunCodingProbe", js)
        self.assertIn("screen_v2", js)
        self.assertIn("holdout_screen_v1", js)
        self.assertIn("coding_probe_v1", js)
        self.assertIn("plan.fullAdaptive.title", js)
        self.assertIn("Provider 模型完整评估", js)
        self.assertIn("临界时自动复测", js)
        self.assertIn("run.adaptiveStageHoldout", js)
        self.assertIn("run.adaptiveHoldoutFallbackPending", js)
        self.assertIn("_holdout_fallback", js)

    def test_model_list_errors_are_actionable(self):
        js = Path("provider_verify_site/app.js").read_text(encoding="utf-8")

        self.assertIn("modelListFailureMessage", js)
        self.assertIn("models.localUnavailable", js)
        self.assertIn("不要使用旧的 8075 页面", js)
        self.assertIn("provider 是否开放 /models 接口", js)
        self.assertIn("待评测模型名来自 provider 自己返回的模型列表", js)
        self.assertIn("AUTH_BLOCKED", js)
        self.assertIn("CONTRACT_INVALID", js)

    def test_mode_selector_has_mode_aware_ui_targets(self):
        html = Path("provider_verify_site/index.html").read_text(encoding="utf-8")

        self.assertIn('id="quickScreenTitle"', html)
        self.assertIn('id="quickScreenNote"', html)
        self.assertIn('id="quickScreenSubmit"', html)

    def test_app_updates_mode_copy_when_selection_changes(self):
        js = Path("provider_verify_site/app.js").read_text(encoding="utf-8")

        self.assertIn("syncEvalModeUi", js)
        self.assertIn("quickScreenTitle", js)
        self.assertIn("quickScreenSubmit", js)
        self.assertIn("Start full assessment", js)
        self.assertIn("Complete Assessment", js)

    def test_index_exposes_language_switcher(self):
        html = Path("provider_verify_site/index.html").read_text(encoding="utf-8")

        self.assertIn('id="languageSwitcher"', html)
        self.assertIn('data-lang="zh"', html)
        self.assertIn('data-lang="en"', html)
        self.assertIn('data-i18n="app.title"', html)

    def test_frontend_has_no_external_font_dependency(self):
        html = Path("provider_verify_site/index.html").read_text(encoding="utf-8")
        css = Path("provider_verify_site/styles.css").read_text(encoding="utf-8")

        self.assertNotIn("fonts.bunny.net", html)
        self.assertNotIn("fonts.bunny.net", css)
        self.assertNotIn("Geist", css)
        self.assertNotIn("Fraunces", css)
        self.assertNotIn("JetBrains Mono", css)

    def test_app_contains_bilingual_copy_and_switch_logic(self):
        js = Path("provider_verify_site/app.js").read_text(encoding="utf-8")

        self.assertIn("UI_COPY", js)
        self.assertIn("zh:", js)
        self.assertIn("en:", js)
        self.assertIn("setLanguage", js)
        self.assertIn("syncLanguageUi", js)
        self.assertIn("模型评测控制台", js)
        self.assertIn("Evaluation Console", js)

    def test_frontend_explains_current_run_and_local_history_scope(self):
        html = Path("provider_verify_site/index.html").read_text(encoding="utf-8")
        js = Path("provider_verify_site/app.js").read_text(encoding="utf-8")

        self.assertIn('class="intro-panel"', html)
        self.assertIn('class="result-guide"', html)
        self.assertIn('class="local-overview"', html)
        self.assertIn('data-i18n="summary.body"', html)
        self.assertIn("把模型声明变成可审计证据", js)
        self.assertIn("先读取 provider 自己返回的模型列表", js)
        self.assertIn("把能力、代码可用性和路由风险分开呈现", js)
        self.assertNotIn("SeerBench reference", html)
        self.assertNotIn("按 SeerBench 的阅读方式组织本地证据", html)
        self.assertIn("这些数字只统计你本机保存过的历史评测记录", js)
        self.assertIn("Reading priority", js)
        self.assertIn("Turn model claims into auditable evidence", js)
        self.assertIn("not the final verdict of the current run", js)

    def test_app_maps_machine_values_to_human_readable_labels(self):
        js = Path("provider_verify_site/app.js").read_text(encoding="utf-8")

        self.assertIn("const VALUE_LABELS", js)
        self.assertIn('TIER_STRONG: "强能力"', js)
        self.assertIn('route_unverified: "路由未验证"', js)
        self.assertIn('coding_trial: "受控 coding 试用"', js)
        self.assertIn('LIMITED_USE: "受限使用"', js)
        self.assertIn('TRIAL_RECOMMENDED: "建议试用"', js)
        self.assertIn('RERUN_REQUIRED: "需要重跑"', js)
        self.assertIn('TIER_STRONG: "Strong"', js)
        self.assertIn('route_unverified: "Route unverified"', js)
        self.assertIn('coding_trial: "Controlled coding trial"', js)
        self.assertIn('LIMITED_USE: "Limited use"', js)
        self.assertIn('TRIAL_RECOMMENDED: "Trial recommended"', js)
        self.assertIn('RERUN_REQUIRED: "Rerun required"', js)
        self.assertIn("function renderDisplayValue", js)
        self.assertIn('class="display-value"', js)
        self.assertIn('class="raw-value"', js)
        self.assertIn("detail.rawValue", js)
        self.assertIn('title="${escapeHtml(raw)}"', js)

    def test_detail_panel_renders_task_level_breakdown(self):
        js = Path("provider_verify_site/app.js").read_text(encoding="utf-8")

        self.assertIn("renderExecutiveSummary(item)", js)
        self.assertIn("renderScoreRationale(item)", js)
        self.assertIn("function buildExecutiveSummary", js)
        self.assertIn('class="detail-hero', js)
        self.assertIn("score-rationale", js)
        self.assertIn("detail.scoreRationale", js)
        self.assertIn("detail.scoreFormula", js)
        self.assertIn("detail.scoreCoverage", js)
        self.assertIn("detail.scoreLimits", js)
        self.assertIn("detail.summaryConclusion", js)
        self.assertIn("detail.summaryReason", js)
        self.assertIn("detail.summaryNextAction", js)
        self.assertIn("renderTaskBreakdown(item.task_results)", js)
        self.assertIn("function renderTaskBreakdown", js)
        self.assertIn("TASK_METADATA", js)
        self.assertIn("taskMeta(task.task_id)", js)
        self.assertIn("scoreMeter(primary", js)
        self.assertIn("detail.verdictScope", js)
        self.assertIn("detail.decisionV2", js)
        self.assertIn("detail.screenScore", js)
        self.assertIn("detail.codingAxisScore", js)
        self.assertIn("detail.coreCapabilityScore", js)
        self.assertIn("detail.workflowCompatibilityScore", js)
        self.assertIn("core_capability_score", js)
        self.assertIn("workflow_compatibility_score", js)
        self.assertIn("score_groups", js)
        self.assertIn("detail.taskBreakdown", js)
        self.assertIn("detail.noTaskResults", js)
        self.assertIn('class="task-list"', js)
        self.assertIn('class="task-row"', js)
        self.assertIn("displayLabel(task.task_id)", js)
        self.assertIn("renderBadge(task.task_status)", js)
        self.assertIn("task.evidence_flags", js)
        self.assertIn("detail.taskPurpose", js)
        self.assertIn("detail.taskRule", js)
        self.assertIn("缺少必需段落", js)
        self.assertIn("本地测试通过", js)

    def test_app_labels_agent_tool_use_scope_without_default_mode_entry(self):
        html = Path("provider_verify_site/index.html").read_text(encoding="utf-8")
        js = Path("provider_verify_site/app.js").read_text(encoding="utf-8")

        self.assertIn("agent_tool_use", js)
        self.assertIn("tool_use_schema_triage", js)
        self.assertIn("agent_tool_use_score", js)
        self.assertIn("detail.agentToolUseScore", js)
        self.assertIn("Structured tool-planning evidence", js)
        self.assertIn("not live tool execution", js)
        self.assertNotIn('value="agent_tool_use_v1"', html)

    def test_run_result_shows_pipeline_progress(self):
        js = Path("provider_verify_site/app.js").read_text(encoding="utf-8")
        css = Path("provider_verify_site/styles.css").read_text(encoding="utf-8")

        self.assertIn("renderRunProgress", js)
        self.assertIn("run.progress.title", js)
        self.assertIn("run.progress.cost", js)
        self.assertIn("run.progress.screen", js)
        self.assertIn("run.progress.holdout", js)
        self.assertIn("run.progress.coding", js)
        self.assertIn("run.step.running", js)
        self.assertIn("run.checks.summary", js)
        self.assertIn("run.checks.scoreFormula", js)
        self.assertIn("function renderScreenCheckPanel", js)
        self.assertIn("summarizeCheckResults", js)
        self.assertIn('class="assessment-checks"', js)
        self.assertIn("setRunResult(message, type = \"neutral\", html = false)", js)
        self.assertIn('class="run-progress"', js)
        self.assertIn(".run-progress", css)
        self.assertIn(".progress-steps", css)
        self.assertIn(".assessment-checks", css)
        self.assertIn(".assessment-check-grid", css)

    def test_report_index_falls_back_to_public_example(self):
        js = Path("provider_verify_site/app.js").read_text(encoding="utf-8")

        self.assertIn("function fetchReportJson", js)
        self.assertIn('fetch("data/report_index.json"', js)
        self.assertIn('fetch("data/report_index.example.json"', js)
        self.assertIn("state.report = await fetchReportJson()", js)

    def test_detail_summary_uses_actionable_chinese_report_copy(self):
        js = Path("provider_verify_site/app.js").read_text(encoding="utf-8")

        self.assertIn("结论", js)
        self.assertIn("为什么这样判定", js)
        self.assertIn("建议下一步", js)
        self.assertIn("这条记录只覆盖代码能力", js)
        self.assertIn("用修正后的代码探针重跑", js)
        self.assertIn("不要把它当成已验证官方模型", js)

    def test_frontend_surfaces_score_scope_before_score(self):
        js = Path("provider_verify_site/app.js").read_text(encoding="utf-8")

        self.assertIn("Strong Evidence", js)
        self.assertIn("Evidence score", js)
        self.assertIn("primaryScoreScope", js)
        self.assertIn("renderBadge(primaryScoreScope(item))", js)
        self.assertIn("Screen triage", js)
        self.assertIn("Coding-only", js)
        self.assertNotIn('"summary.capabilityOk.label": "Capability OK"', js)
        self.assertNotIn('"table.score": "Score"', js)

    def test_detail_human_labels_keep_natural_casing(self):
        css = Path("provider_verify_site/styles.css").read_text(encoding="utf-8")

        self.assertIn(".kv strong .display-value", css)
        self.assertIn("text-transform: none", css)

    def test_mobile_layout_keeps_wide_table_scroll_inside_panel(self):
        css = Path("provider_verify_site/styles.css").read_text(encoding="utf-8")

        self.assertIn(".workspace > *", css)
        self.assertIn("min-width: 0", css)


if __name__ == "__main__":
    unittest.main()
