# Opus 4.8 Review Packet

Cross-agent packet

Mode: review
Mainline owner: Codex
Executor lane: none
Review lane required: yes
Review lane mode: review/challenge
Risk level: L1
L3 actions requested: none
Stop before commit/push/real API/live system: yes

Objective:
- Review the local Provider Verify Site v0 for `model_evaluate`.
- Challenge whether the schema, UI, and method boundary correctly support
  repeated provider-route evaluation without overclaiming model identity.

Authoritative artifacts:
- `docs/plans/2026-06-14-provider-verify-site-v0-design.md` - local-draft
- `docs/plans/2026-06-14-provider-verify-site-v0-implementation.md` - local-draft
- `provider_verify_site/scripts/build_site_data.py` - local-draft
- `provider_verify_site/tests/test_build_site_data.py` - local-draft
- `provider_verify_site/index.html` - local-draft
- `provider_verify_site/styles.css` - local-draft
- `provider_verify_site/app.js` - local-draft
- `provider_verify_site/data/report_index.json` - generated local-draft
- `provider_verify_site/README_zh.md` - local-draft

Approved decisions:
- v0 is a local evidence viewer, not a live endpoint tester.
- v0 must not read API keys, call provider endpoints, run production checks, or
  mutate existing evaluation records.
- Capability evidence, identity evidence, route transparency, routing risk, and
  coding usability must remain separate.
- A 500K/1M-class context pass may support capability-class evidence but must
  not by itself become `identity_verified`.

Do now:
1. Read the artifacts listed above.
2. Review schema labels for overclaiming or ambiguity.
3. Review UI fields for repeated provider comparison usefulness.
4. Review whether evidence flags are too weak, too strong, or missing important
   failure classes.
5. Review whether the implementation preserves raw evidence and avoids secrets.

Do not:
- Do not edit files.
- Do not run live API calls.
- Do not read API keys, environment variables, provider configs, cc-switch DB,
  or credential stores.
- Do not perform provider identity probes.
- Do not create an alternative implementation plan from scratch.
- Do not mark any provider route as `identity_verified`.

Reply with:
- accepted facts
- concerns by severity
- recommended schema/UI changes
- missing tests or verification gaps
- boundary check
- final recommendation: accept v0 / revise before use / block
