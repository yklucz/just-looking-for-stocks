# Agentic stock research roadmap

*Generated 2026-09-22 | Scope: research automation first, no autonomous live trading | Confidence: high on architecture, low on any claim of future alpha*

**Implementation status (2026-09-22):** The separately scoped Phase 0 operational
foundation is implemented in [`stock_app.jobs`](../stock_app/jobs/__main__.py).
It provides persistent external-triggered runs, guarded idempotency, coalesced
missed-run recovery, bounded retries and source health. See
[operations and limitations](operational-jobs.md). This implements the scheduling
foundation listed below under Phase 1; the roadmap's research conclusions and
phase numbering remain unchanged. Forecast-ledger canonicalization (Phase 0.2)
is implemented with immutable child revisions, first-accepted causal authority,
additive migration and a read-only integrity audit; see the
[identity and migration contract](canonical-forecast-ledger.md). The separately scoped Phase 1 immutable Research Experiment Registry is implemented
(2026-09-23), with preregistration, retained attempts/outcomes, trial-family history,
verified legacy import, and read-only audit; see [registry contract and verification](research-experiment-registry.md).
The separately scoped Phase 1.5 evidence, reproducibility and reconciliation layer is implemented
(2026-09-23); see [research integrity](research-integrity.md). The separately scoped Phase 2A [point-in-time foundation](point-in-time-data.md) is implemented
(2026-09-23), with offline provider contracts and no live backfill. Phase 2B has not started.
The AI research agent remains unimplemented. External scheduler installation
and live-provider verification are deployment steps, not completed local tests.

## Executive summary

This repository should become an **auditable AI research analyst**, not a self-modifying trading bot. The agent should gather time-valid evidence, propose falsifiable hypotheses, submit typed experiment specifications, call deterministic research tools, challenge the results, and produce cited reports. It should not calculate returns in free-form text, rewrite its own production rules, promote models, or hold broker credentials.

The existing platform is an unusually strong foundation for this direction. It already has immutable snapshots, checksums, causal features, purged walk-forward comparisons, calibration, a forecast ledger, candidate/shadow/active model states, prospective qualification, manual activation, rollback, and durable local job records. The main weaknesses are data breadth and point-in-time coverage, a narrow fixed universe and target, limited execution realism, no portfolio-level decision layer, no multiple-testing ledger, and no LLM/agent orchestration or evaluation harness.

The most important local evidence is negative: all eight initial selected procedures failed to beat their simple comparator on average primary error. The agent should therefore automate **disciplined rejection and learning**, not automatic deployment. The desired improvement loop is:

```text
observe failures -> propose a registered hypothesis -> build time-valid data
-> run a sealed deterministic evaluation -> independent critique
-> reject or freeze as challenger -> prospective shadow evidence
-> human approval -> canary -> monitor -> rollback
```

A current local audit also found two operational issues to fix before an agent increases job volume: the newest stored Yahoo session was 2026-09-15 on 2026-09-22 because scheduling stops with the Flask process, and the forecast ledger contained hundreds of revisions for one AAPL origin because input snapshot identity participates in issuance identity. These are precisely the kinds of small automation defects that an agent would amplify.

## 1. What exists today

The current path is approximately:

```text
Yahoo OHLCV + present-day market/sector context
    -> immutable local snapshots and checksums
    -> calendar and availability validation
    -> causal technical/context features
    -> fixed five-session classification or return target
    -> purged walk-forward selection and calibration
    -> candidate artifact
    -> manual shadow nomination
    -> daily prospective forecast ledger
    -> 126 matched resolved origins + confidence-bound qualification
    -> manual activation or rollback
```

Evidence in this repository:

- [`README.md`](../README.md) describes the causal feature, model, dashboard, and backtest flow and explicitly says the application neither trades nor claims reliable predictive value.
- [`research-platform.md`](research-platform.md) documents immutable inputs, replay, calendar validation, candidate/shadow/active lifecycle, prospective issuance, scheduling, and rollback.
- [`runtime.py`](../stock_app/research/runtime.py) implements one durable local worker that refreshes daily, issues forecasts, and runs monthly binary/regression experiments.
- [`lifecycle.py`](../stock_app/research/lifecycle.py) requires frozen prospective evidence and retains manual activation.
- [`research-initial-results.md`](research-initial-results.md) reports that 8/8 selected procedures failed to beat simple comparators on average primary error; this is a research platform with no demonstrated forecasting edge.

### Highest-value gaps

1. **Point-in-time coverage is incomplete.** Yahoo prices are current-vintage adjusted data, not original tape or a historical corporate-action database. Yahoo company context is explicitly unverified and excluded from training. There is no filing-time SEC fundamentals pipeline, macro vintage store, licensed news archive, or security master with delistings and symbol history.
2. **The tested problem is narrow.** The initial universe is four symbols, the expanded list is present-day selected, and the core target is one five-session event plus return regression. This cannot establish cross-sectional or regime-general skill.
3. **Research search is not accounted for.** The platform compares fixed configurations correctly, but an agent that generates many ideas creates a new multiple-testing problem. Every attempted hypothesis, revision, metric, universe, and stopping decision must enter a trial ledger.
4. **Backtests are not yet an execution-grade market simulator.** Directional/error metrics are useful, but economic claims also require quote-aware fills, spread, latency, partial fills, capacity, fees, borrow/short constraints, and implementation shortfall.
5. **There is no portfolio/risk layer.** Per-symbol LONG/FLAT estimates are not a portfolio policy. Position sizing, exposure constraints, covariance, liquidity, cash, turnover, drawdown and tail risk must be deterministic services.
6. **There is no agent layer yet.** The pinned dependencies contain statistical/ML and Flask libraries, but no LLM client, agent runtime, trace evaluator, prompt registry, or agent-specific test set.
7. **Local operations are not production operations.** The scheduler only runs while the server/computer is on, SQLite has one local worker, and the app is intentionally localhost-only without multi-user authentication.
8. **Forecast revision cardinality needs a canonical key.** The current issuance identity includes the input snapshot hash, so repeatedly revised inputs can create many records for one model/origin. Add one canonical `(model_id, origin, horizon, issuance_kind)` record with child input revisions, retention/compaction, and duplicate-rate alerts before increasing autonomous refresh or experiment volume.

## 2. Market and information flow to model

A research-grade market flow should be represented as:

```text
licensed price/quote feeds + SEC filings + corporate actions + macro releases + dated news
    -> append-only raw events
    -> permanent security identities and corporate-action versions
    -> bitemporal point-in-time store
       (event_time, available_at, ingested_at, revision_id)
    -> validated feature views at a declared decision timestamp
    -> signal/model forecasts with provenance
    -> portfolio and risk constraints
    -> simulated order intents and execution
    -> fills, positions, cash and corporate-action reconciliation
    -> forecast, portfolio and transaction-cost evaluation
```

Why this matters:

- U.S. equities trade across exchanges and off-exchange venues; feed coverage is therefore part of the experiment definition. Persist provider/feed, venue or tape, quote/trade conditions, event time, ingestion time, and sequence where supplied. Do not train on a consolidated feed and silently operate on a single-venue feed. [FINRA OTC transparency](https://www.finra.org/filing-reporting/otc-transparency), [Alpaca market-data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)
- SEC EDGAR provides real-time submissions history and extracted XBRL facts. Fundamental features should be keyed by CIK, accession, form, fiscal period, acceptance time and amendment; period end is not availability time. [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- ALFRED distinguishes what is known now from what was known on a past date. Macro backtests must query the vintage available at the simulated decision time. [FRED versus ALFRED](https://fred.stlouisfed.org/docs/api/fred/fred_vs_alfred.html)
- Corporate actions are versioned events, not merely a pre-adjusted close column. Retain raw prices and reproducible adjustment factors, including announcement, ex, record, payable and revision timestamps. [NYSE corporate actions](https://www.nyse.com/market-data/corporate-actions)
- Execution quality depends on order type and size, price improvement, speed, fill likelihood, routing and costs. Economic evaluation should report arrival-price slippage, effective spread, realized spread, fill rate and implementation shortfall. [FINRA best execution](https://www.finra.org/rules-guidance/guidance/reports/2024-finra-annual-regulatory-oversight-report/best-execution)

## 3. Recommended agent architecture

```text
User question
    -> policy/scope router
    -> planner
       -> evidence collector
       -> hypothesis designer
       -> experiment submitter
    -> deterministic data/features/backtest/portfolio tools
    -> independent critic and model-risk reviewer
    -> cited report composer
    -> human decision
```

### Components

**Policy and scope router**

- Enforces research-only behavior.
- Provides no broker or order-submission tool.
- Sets universe, horizon, data cutoff, risk class, token/tool/runtime budget, and allowed data sources.
- Treats all downloaded filings, news, webpages and tool results as untrusted data that cannot redefine system instructions.

**Planner/orchestrator**

- Emits a typed `ResearchPlan`, not prose-only intentions.
- Must declare the question, causal thesis, falsification condition, universe, horizon, information cutoff, baselines, metrics, planned trials and stop conditions before results are visible.
- Can call only allow-listed, schema-validated tools.

**Evidence collector**

- Retrieves SEC filings/XBRL, price/quote snapshots, corporate actions, ALFRED vintages and approved news sources.
- Writes claim/source links, retrieval and publication timestamps, checksums, licensing metadata and parse warnings to the evidence ledger.
- Rejects evidence whose `available_at` exceeds the experiment decision time.

**Hypothesis laboratory**

- Converts narrative ideas into testable feature/target/portfolio specifications.
- Registers each idea and revision before evaluation, including parent hypothesis and trial family.
- Does not execute arbitrary generated Python against production data. It submits a restricted experiment schema to deterministic code.

**Deterministic experiment service**

- Owns feature computation, splits, fitting, P&L, statistics and artifacts.
- Extends the existing experiment runner with portfolio simulation, realistic transaction costs, capacity constraints, regime slices, ablations and a multiple-testing report.
- Produces machine-readable results; the LLM explains them but cannot change them.

**Independent critic**

- Receives the frozen plan, evidence and outputs in a separate context.
- Searches for leakage, survivorship bias, weak baselines, target/metric mismatch, data revisions, omitted costs, instability, unsupported causal stories and suspicious stopping.
- Can request deterministic reruns but cannot edit the original result.

**Report composer**

- Separates observed facts, calculations, model outputs, hypotheses and inferences.
- Includes citations, hashes, model/tool versions, failed checks, uncertainty, limits and invalidation triggers.

**Trace and governance plane**

- Stores every plan, tool call, tool result, source, prompt/model version, approval, cost, error, experiment and artifact hash.
- Implements least privilege, secret isolation, time/cost/retry caps, cancellation, incident logging and rollback.

Current financial-agent evidence argues for this constrained design. The 2025 Finance Agent Benchmark reports 46.8% accuracy for its best model on 537 expert-authored filing-based finance tasks, and StockBench reports that most tested LLM agents did not beat buy-and-hold. These are not direct evaluations of this repository, but they are strong evidence against unsupervised financial authority. [Finance Agent Benchmark](https://arxiv.org/abs/2508.00828), [StockBench](https://arxiv.org/abs/2510.02209)

## 4. Safe automated improvement

“Improve itself” should mean **generate and evaluate candidates under fixed rules**, not edit production autonomously.

```text
production research traces + user corrections + forecast outcomes
    -> redact, normalize and label failures
    -> add immutable regression/evaluation cases
    -> propose candidate prompt, workflow, feature or model changes
    -> replay on frozen agent and finance evaluation sets
    -> independent safety and statistical review
    -> champion/challenger comparison
    -> human approval
    -> limited research canary
    -> monitor and automatically roll back on gate failure
```

### What may improve automatically

- Retrieval queries and source ranking, within an allow-list.
- Experiment proposals and prioritization.
- Candidate prompts, routing policies and tool-use plans in an offline branch.
- Candidate statistical models and feature sets.
- Failure taxonomy and new evaluation cases from reviewed traces.
- Scheduling based on stale data, drift alerts and unresolved research questions.

### What must remain fixed or human-controlled

- Tool permissions, secret access and broker boundary.
- Evaluation datasets, primary success metrics and risk limits.
- The rules for prospective qualification and multiple-testing correction.
- Production prompt/workflow promotion.
- Model activation, capital allocation and any order submission.

### Promotion gates

1. **Agent quality:** exact calculation, claim support, citation correctness/freshness, correct tool choice, reproducibility, budget adherence and graceful failure.
2. **Finance validity:** point-in-time integrity, survivorship-free universe, simple baselines, nested/purged validation, trial-count disclosure, stability across regimes and symbols, calibrated uncertainty, and no test-set reuse.
3. **Economic validity:** net performance after conservative spread, fees, slippage, delay and capacity; turnover, drawdown, tail loss and implementation shortfall.
4. **Safety:** prompt-injection resistance, schema validation, no unapproved writes, no secret leakage, and no unsupported personalized investment claim.
5. **Operations:** deterministic replay, complete trace, provider failure handling, latency/cost limits, canary health and rollback.

Backtest selection must count as model search. Ordinary holdouts can fail after enough strategy trials; maintain a family-level trial ledger and add probability-of-backtest-overfitting or equivalent multiple-comparison analysis rather than reporting only the best Sharpe. [The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/Papers.cfm?abstract_id=2326253)

The Federal Reserve's revised 2026 model-risk guidance is not directly binding on this personal research application and explicitly has a defined banking scope. Its principles are nevertheless a useful governance analogue: conceptual soundness, independent challenge, outcomes analysis, ongoing monitoring, documented limitations, inventory, accountability and validation of third-party products. [Federal Reserve revised model-risk guidance](https://www.federalreserve.gov/supervisionreg/srletters/SR2602.htm)

## 5. Research agent versus trading agent

| Capability | Research agent now | Possible execution system later |
|---|---|---|
| Read market data and filings | Yes | Yes |
| Generate hypotheses | Yes | No production authority |
| Run deterministic backtests | Yes | Consumes approved signals only |
| Nominate a shadow candidate | May recommend | Human-controlled |
| Activate a model | No | Human-controlled |
| Access broker credentials | No | Separate isolated service |
| Submit an order | No | Signed order ticket through deterministic risk engine |

If execution is ever added, make it a separate service and repository boundary. The research agent may create a proposed order ticket, but a non-LLM risk engine must enforce symbol allow-lists, stale-data rejection, market-hours rules, price collars, per-order/daily notional limits, gross/net/sector exposure, duplicate protection, restricted lists, borrow constraints, kill switch and reconciliation. Human approval should remain required initially. SEC Rule 15c3-5 applies to broker-dealers with market access, not automatically to this current app, but its pre-trade threshold, erroneous-order, authorization and review principles are the correct safety benchmark. [SEC Market Access Rule](https://www.sec.gov/rules-regulations/2011/06/risk-management-controls-brokers-or-dealers-market-access)

## 6. Prioritized implementation roadmap

### Phase 0 — define the product and gates (1 week)

- Adopt the product promise: “auditable AI research analyst,” not “AI that beats the market.”
- Define typed schemas: `ResearchPlan`, `EvidenceItem`, `Hypothesis`, `ExperimentSpec`, `Critique`, `ResearchReport`.
- Create tool risk classes and explicitly forbid broker/order tools.
- Freeze evaluation gates and approval ownership before adding an LLM.

### Phase 1 — evidence and time integrity (2–4 weeks)

- Move scheduling out of the Flask daemon thread to a durable local scheduler/queue with missed-run recovery, stale-data alerts, provider backoff and health checks.
- Normalize the forecast ledger around a canonical issuance key and child revisions; add cardinality invariants and compaction.
- Add an `evidence` table and append-only raw-object store.
- Extend every record with `event_time`, `available_at`, `ingested_at`, `revision_id`, source, checksum and license/use metadata.
- Add SEC submissions/XBRL ingestion keyed by CIK/accession/acceptance time.
- Add ALFRED vintage ingestion for a small macro set.
- Add a security master with permanent IDs, ticker history, listings/delistings and versioned corporate actions.
- Add invariant tests that fail whenever any feature was unavailable at the simulated decision time.

### Phase 2 — deterministic research tools (3–5 weeks)

- Wrap existing refresh, experiment, replay, forecast and export operations in strict typed tool contracts.
- Add `register_hypothesis`, `run_experiment`, `compare_trials`, `critique_inputs`, and `render_report` APIs.
- Add a complete trial ledger and family identifier; never delete failed experiments.
- Add universe, regime, target and metric registries so variations are explicit and hashable.
- Add portfolio simulation and execution assumptions before any P&L-based promotion.

### Phase 3 — first agent workflow (2–4 weeks)

- Implement one orchestrator plus one independent critic; avoid a large multi-agent society initially.
- Give the orchestrator read-only evidence tools and bounded experiment submission.
- Use structured outputs and schema validation at every boundary.
- Add prompt/tool/model versioning, trace capture, token/runtime budgets and citation checks.
- Ship cited research reports and experiment proposals; do not change current prediction outputs yet.

### Phase 4 — evaluation flywheel (ongoing, first usable gate in 3–4 weeks)

- Build a frozen task set from SEC filing extraction, point-in-time questions, hand-calculated metrics, leakage traps, failed-provider cases and existing repository regressions.
- Grade final answers and trajectories separately.
- Add reviewed production failures as new immutable cases without changing the old cases.
- Compare candidate prompts/workflows/models offline; require human promotion, canary and rollback.

### Phase 5 — paper portfolio and execution realism (4–8 weeks)

- Add quote-aware simulated orders, spreads, latency, partial fills, capacity, fees and corporate-action/cash accounting.
- Maintain a broker-free paper ledger first; reconcile expected versus simulated fills and forecast error versus net P&L.
- Add drift and degradation alerts. Drift may trigger a research proposal or shadow retraining, never automatic active replacement.

### Phase 6 — optional isolated execution (only after separate review)

- Obtain legal/compliance, security and operational review for the actual jurisdiction, operator and user model.
- Create a separate authenticated execution service with narrow signed tickets and deterministic pre-trade controls.
- Start with tiny capped exposure, explicit approval for every order, complete reconciliation and a tested kill switch.

## 7. First concrete backlog

1. `research/runtime.py`: external scheduler integration, heartbeat, missed-run recovery and stale-data SLOs.
2. `research/ledger.py`: canonical issuance identity, child input revisions, cardinality invariants and compaction.
3. `research/evidence.py`: bitemporal evidence schema and point-in-time query.
4. `research/sec.py`: SEC submissions/XBRL adapter with acceptance timestamps and amendment lineage.
5. `research/macro.py`: ALFRED vintage adapter.
6. `research/security_master.py`: permanent IDs, ticker history, delistings and corporate actions.
7. `research/hypotheses.py`: immutable hypothesis and trial-family ledger.
8. `research/tools.py`: typed, allow-listed adapters around existing deterministic operations.
9. `research/agent/`: planner, policy router, critic and report composer; no broker package dependency.
10. `research/evals/`: frozen finance tasks, leakage traps, citation/tool-trajectory graders.
11. `backtest/execution.py`: extend to quotes, partial fills, capacity and implementation shortfall.
12. `backtest/portfolio.py`: explicit exposure, liquidity, turnover and risk constraints.

The first milestone should be: **Given a research question, produce a cited, replayable report and one pre-registered experiment proposal without changing any active model.** That creates real agentic value while keeping the statistical and operational risk bounded.

## Sources and methodology

Research covered market/data flow, point-in-time data, execution and settlement, agent reliability, model risk, backtest overfitting and the repository's current implementation. Priority was given to official SEC, FINRA, NYSE, Federal Reserve/NIST material and primary papers. Key sources include:

1. [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) — filing and XBRL access/update behavior.
2. [FRED versus ALFRED](https://fred.stlouisfed.org/docs/api/fred/fred_vs_alfred.html) — current versus historical-vintage macro data.
3. [NYSE corporate actions](https://www.nyse.com/market-data/corporate-actions) — event/reference-data coverage.
4. [FINRA algorithmic trading](https://www.finra.org/rules-guidance/key-topics/algorithmic-trading) — testing, validation, review and supervision principles.
5. [FINRA Regulatory Notice 24-09](https://www.finra.org/rules-guidance/notices/24-09) — technology-neutral obligations and GenAI risks.
6. [SEC Market Access Rule](https://www.sec.gov/rules-regulations/2011/06/risk-management-controls-brokers-or-dealers-market-access) — pre-trade and supervisory control benchmark.
7. [NIST AI RMF and GenAI profile](https://www.nist.gov/itl/ai-risk-management-framework) — governance, mapping, measurement and management.
8. [Federal Reserve revised model-risk guidance](https://www.federalreserve.gov/supervisionreg/srletters/SR2602.htm) — current risk-based model governance principles.
9. [Finance Agent Benchmark](https://arxiv.org/abs/2508.00828) — real-world finance research agent limitations.
10. [StockBench](https://arxiv.org/abs/2510.02209) — sequential trading-agent evaluation and baseline results.
11. [The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/Papers.cfm?abstract_id=2326253) — search/selection bias in investment backtests.

This report does not claim that any proposed model will generate alpha, that cited regulatory rules apply to this personal project, or that software correctness establishes investment performance. Those questions require prospective evidence and, for live execution or personalized advice, jurisdiction-specific professional review.
