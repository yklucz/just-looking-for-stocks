# Phase 2B repository wrap-up — 2026-09-24

This pass closes the completed roadmap work without starting Phase 2C. The live
Phase 2B.1 gate remains unmet: SEC_USER_AGENT and FRED_API_KEY were absent in the
recorded pilot attempt. No live provider validation is claimed.

## Initial Git state

Branch `codex/research-platform`, tracking `origin/codex/research-platform`, was
one commit ahead (`a8c3232`). Origin is
`https://github.com/yklucz/just-looking-for-stocks.git`.
There were ten modified tracked files, eleven untracked source/test/documentation
files, and no staged files. All belonged to Phase 2B/2B.1. Operational evidence,
SQLite databases/backups and logs under `artifacts/` were ignored. No tags existed.

## Fixes and regression evidence

| Classification | Finding | Resolution |
| --- | --- | --- |
| BUG / SECURITY | An unknown transport provider fell through to the ALFRED branch without provider-specific configuration validation. The destination remained fixed, but the provider allowlist was not enforced. | Reject unknown providers before transport or credential use. One regression failed before the fix and passes afterward. |
| RELIABILITY | HTTP response ownership was not explicitly finalized across return, rejection and retry paths. Requests normally consumes bodies, but explicit closure was missing at this boundary. | Close responses in `finally`, before retry sleep. Six regression cases cover success, permanent error, throttling, server error, long Retry-After and credential echo rejection. All failed before the fix. |
| RELIABILITY | Registry temporary files survived write/flush/fsync failure because cleanup began only after the write completed. | Extend cleanup to the entire creation/publication sequence. Injected fsync failure reproduced the orphan; fsync and link failure tests now pass. |
| MAINTAINABILITY | Two nested conditional expressions in integrity verification (S3358). | Expand into statements, preserving threshold and result classification. |
| MAINTAINABILITY | Unused `sha256` and `Path` imports in PIT storage. | Remove imports only. |

Cleanup touched `stock_app/research/pit/transport.py`,
`stock_app/research/registry_execution.py`, `stock_app/research/integrity.py`,
`stock_app/research/pit/store.py`, `tests/test_pit_collection.py`,
`tests/test_registry_artifact_cleanup.py`, and this report. Other staged changes
are the pre-existing Phase 2B/2B.1 work.

## Editor diagnostics and static review

VS Code's installed SonarQube extension was inspected directly. Findings varied
with open files/reanalysis: an earlier panel reported 40 Sonar warnings, while
later per-file panels exposed the findings below. There is no configured
repository-wide Sonar scanner or command-line diagnostic export. Consequently,
this is **not a certified exhaustive 40-warning inventory or a clean Sonar quality
gate**. The individually captured diagnostics and source review are recorded here;
uncaptured warning locations are not represented as resolved.

| Diagnostic / original location | Classification | Disposition |
| --- | --- | --- |
| integrity.py:98 S3776, complexity 28 | MAINTAINABILITY | Retain explicit snapshot identity, containment and checksum checks; broad extraction deferred. |
| integrity.py:135 S3776, complexity 181 (183 after statement expansion) | MAINTAINABILITY | Preserve detailed evidence contracts; broad verification refactor deferred. |
| integrity.py:174 S3358 | MAINTAINABILITY | Fixed; refreshed editor no longer lists it. |
| integrity.py:343 S3358 | MAINTAINABILITY | Fixed; refreshed editor no longer lists it. |
| stock_service.py:254 S3776, complexity 17 | MAINTAINABILITY | Leave established cache/range/fallback behavior unchanged. |
| health.py:50 S3776, complexity 19 | MAINTAINABILITY | Existing multi-source health reporting retained. |
| research/api.py:36 S3776, complexity 59 | MAINTAINABILITY | Existing typed export/filter dispatch retained. |
| research/api.py:105 S3358 | MAINTAINABILITY | Existing CSV value conversion retained; no changed escaping contract. |
| inference.py:132 S3776, complexity 17 | MAINTAINABILITY | Forecast/inference semantics protected; no broad refactor. |
| integrity_reconcile.py:33 S3776, complexity 139 | MAINTAINABILITY | Explicit historical evidence classification retained. |
| integrity_reconcile.py:52 S3358 | MAINTAINABILITY | Existing checksum/missing/provenance category precedence retained. |
| integrity_reconcile.py:75 S3358 | MAINTAINABILITY | Existing stale-run evidence precedence retained. |
| integrity_reconcile.py:76 S3358 | MAINTAINABILITY | Existing artifact/report fallback classification retained. |
| ledger.py:66 S3776, complexity 23 | MAINTAINABILITY | Existing evaluation branches retained; no forecast changes. |
| pit/transport.py:51 regex backtracking warning | FALSE_POSITIVE for a remotely exploitable vulnerability | Input is local operator-controlled SEC configuration, not request or provider content. No remote input path found; validation retained. This is not a claim of linear regex complexity for arbitrary operator input. |
| pit/transport.py:100 S3776, complexity 58 | MAINTAINABILITY | Explicit credential, retry, redaction and cleanup branches retained. |
| pit/transport.py:127 S3358 | MAINTAINABILITY | Compact three-way retry classification retained. |
| integrity.py:202 Pylance optional subscript | FALSE_POSITIVE | Missing spec sets `legacy=True`; this access is in the `not legacy` branch. |
| stock_service.py:200 two Pylance datetime overload/None errors | FALSE_POSITIVE for observed runtime contract | Scalar provider timestamp or None is passed with coercion; missing values yield no displayed quote timestamp. Existing quote regression passes. |
| stock_service.py:284 Pylance generic Index.tz error | FALSE_POSITIVE for supported provider contract | Yahoo history uses a DatetimeIndex; the DataFrame annotation does not express that index type. |
| pit/transport.py:197 two Pylance observation bound errors | FALSE_POSITIVE | `day()` rejects missing/invalid bounds before parameter construction. Offline invalid-bound tests cover rejection. |

Remaining Sonar warnings are maintainability findings and the local-configuration
regex warning above; the full current repository-wide count is unverified. No
rules were disabled, no blanket exclusions or suppressions were added, and no
formatter/linter/type-checker/dependency was installed.

Targeted source inspection additionally covered scheduler effect fencing and
worker locks, canonical issuance identities, registry immutability, transaction
rollback, PIT as-of/availability selection, collection checkpoints/reparse,
pagination bounds, allowlisted endpoints, redaction, migration preservation,
and frontend DOM/fetch/listener behavior. Dynamic SQL identifiers come from
internal allowlists or quoted SQLite schema names; values remain parameterized.
Trusted-local pickle/joblib loading is INTENTIONAL_DESIGN and remains unchanged.
The frontend's `innerHTML` uses clear containers with an empty constant; provider
values are rendered as text. No frontend change was necessary.

## Validation

- Full suite: **800 passed, 2 skipped** (52.94 seconds), up from 791 passed.
  The two skips remain opt-in, credential-gated live provider smoke tests.
- `python -m compileall -q stock_app tests`: passed.
- `git diff --check`: passed.
- All three Node scripts passed: `test_chart_data.mjs`,
  `test_frontend_contract.mjs`, `test_research_frontend.mjs`.
- `python -m stock_app.research registry audit`: `ok`.
- `python -m stock_app.research forecast-audit`: `healthy`.
- `python -m stock_app.research integrity audit --depth artifact`: `attention`,
  exactly ten `legacy_unverifiable` findings; no new cases/actions materialized.
- `python -m stock_app.research data audit --depth artifact`: `ok`;
  production PIT tables remain empty, so this is not live evidence validation.
- All 31 table count/content hashes match the pre-pilot inventory. All 17 model
  states match: ten existing artifact hashes and seven pre-existing unavailable
  file states. Active bindings, forecast revisions and historical records match.
- Tracked and nonignored untracked text scan found only synthetic test identity
  strings, no real credentials. `.env` and operational artifacts remain ignored.

Local test/audit/preservation outputs are under ignored `artifacts/research/`
with the `wrapup-` prefix. They are not publication artifacts.

The final response records the actual commit hash, push outcome and final Git
status after publication. Phase 2B.1 may be rerun once the operator supplies the
required configuration. Phase 2C remains gated and has not started.
