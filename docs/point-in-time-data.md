# Point-in-time data foundation (Phase 2A)

This is an additive, provider-neutral evidence foundation. Phase 2A itself includes
no network client. The separately scoped [Phase 2B collection pilot](pit-provider-ingestion.md)
adds controlled provider transport and reparse (2026-09-24), with opt-in operational jobs.
Neither phase adds a training consumer, forecast change, security-master import or LLM.
Yahoo prices/context retain their current-vintage semantics.

## Existing path and leakage risks

The existing path is Yahoo download → saved raw CSV → validated/adjusted CSV plus
snapshot metadata → causal features → registered experiment. Phase 0 preserves
jobs, input IDs, effect fencing and locks; Phase 1 pins frame hashes and immutable
reports; Phase 1.5 checks their evidence and replay. Those controls make a snapshot
repeatable, but cannot make a recently downloaded historical series reflect what
was known in the past. Current corporate-action adjustments, restated statements,
provider corrections and present-day symbol membership remain revision and
survivorship risks. The existing view-only company-context path already labels
current-vintage statements as unverified and training-ineligible.

The new path is separately opt-in:

```text
registered source + explicit internal identity
  → immutable raw bytes and retrieval observation
  → typed logical event and append-only temporal revisions
  → explicit cutoff/policy query
  → frozen PIT input manifest
  → future registered consumer + offline integrity/leakage checks
```

No old snapshot or experiment is relabeled PIT. No current-vintage fallback is
available to strict PIT queries. Existing AAPL-style prediction inputs continue
through the unchanged Yahoo/model path.

## Five distinct temporal concepts

| Field | Meaning | Representation |
| --- | --- | --- |
| `event_time`, optional `event_end` | When the fact applies, such as a fiscal period or macro observation date | Nullable `{precision: date|instant, value}` |
| `source_time` | Timestamp/date actually supplied by the provider | Same precision-preserving type |
| Availability contract | Basis on which information may be treated as known | Kind, basis, value, optional explicit timezone/policy |
| `observed_time` | When our collector saw the raw response | Required timezone-aware timestamp inherited from raw evidence |
| `ingested_time` | Local insertion/commit transaction's clock sample | Separate required timestamp, never used as historical publication time |

Exact instants require explicit timezone and normalize to UTC. Dates remain dates;
no implicit midnight UTC conversion occurs. Event time can legitimately precede or
follow publication (e.g. a future effective corporate action); only declared event
range ordering is enforced. Exact source/availability times cannot follow actual
observation; observation cannot follow ingestion. Ingestion times are sampled for
the insert transaction, not a distributed clock or cryptographic timestamp.

## Availability and query policies

`Availability` is a frozen typed value with these kinds:

| Kind | Meaning | Policy behavior |
| --- | --- | --- |
| `exact` | Caller has explicit exact source-availability evidence | Eligible in strict mode at/after the recorded instant |
| `date_level` | Provider establishes a date, not intraday publication | Requires explicit timezone and `end_of_day_v1`; only permitted with `allow_proxy` |
| `proxy` | Explicitly named approximation such as SEC acceptance | Requires a named proxy policy; only permitted with `allow_proxy` |
| `observed_only` | Only local observation is known | Eligible only in `observed_only` mode |
| `unknown` | No defensible historical availability metadata | Excluded from strict/proxy queries; local observation can still be queried explicitly |

`exact` refuses a proxy basis/policy. Source assertions remain evidence claims,
not independent proof of provider truth; use only trusted typed ingestion services.

All queries require `as_of` and `strictness`; neither defaults to today/latest:

- `strict`: exact historical availability only. Date-level, proxy, unknown and
  observed-only facts are excluded explicitly.
- `allow_proxy`: exact plus labeled proxy/date-level facts. Date-level eligibility
  starts at the **next local midnight** under the explicitly supplied timezone.
  The computed `available_bound` is a conservative query bound, not an invented
  source timestamp. DST is respected. This policy does not prove provider-publication
  precision; returned metadata and leakage warnings retain the distinction.
- `observed_only`: observation must be at/before cutoff. Among observed source
  vintages, the latest provable source availability wins; a later download of an
  older vintage cannot replace it. Unknown-only histories use observation order.
  Mixed unknown/known version ordering remains ambiguous rather than guessed.

Within each logical event, select the latest eligible revision. Same-time lineage
must establish a unique head. Identical semantic revisions backed by identical raw
bytes can share the earliest recorded observation as their representative; all
retrievals/revisions remain visible. Conflicting equal-time revisions are returned
as ambiguity, never broken by insertion order. ALFRED realtime intervals are
inclusive date intervals; expired values are excluded even when the next vintage
is absent. Conservative date policy can intentionally leave an intraday gap.

`get_as_of(path, as_of=..., strictness=..., identity_id=..., data_type=...,
source_id=...)` filters explicit internal identities. Entity facts are queried by
entity ID; use the security's recorded parent to inspect its issuer. No inferred
issuer/security join or ticker-history inference is performed.

## Source and security identity

Six additive tables provide the foundation:

- `pit_sources`: permanent `(provider, feed)` identity, source type, schema version,
  temporal capabilities and creation time. SEC submissions/companyfacts/documents
  can use separate feed IDs. Downstream objects reference the source ID by FK.
- `pit_identities`: typed permanent internal entity → security → listing hierarchy.
  SQL requires securities to have an entity parent and listings to have a security
  parent. Listings require exchange identity through the service. Caller-supplied
  internal keys and mapping evidence are mandatory; display names are not IDs.
- `pit_identifiers`: typed external aliases, source/evidence, optional raw reference,
  scope (e.g. exchange) and nullable half-open `[valid_from, valid_to)` dates.
  Null dates remain unknown. An ongoing interval requires explicit `open_ended=True`
  with a known start; unknown end never silently means infinite validity.
  CIK can identify an entity only. ISIN/CUSIP/provider aliases remain distinct.
- `pit_raw`: immutable retrieval observations referencing content-addressed bytes.
- `pit_events`: unique source/type/provider logical key, optional entity/security/
  listing relationship.
- `pit_revisions`: append-only versions, composite source FKs, raw evidence,
  lineage, availability and distinct observation/ingestion clocks.

An entity acts as issuer when it owns securities; no CIK, accession or ticker is
converted to a permanent security ID. Multiple securities and listings are supported.
The architecture is **not a globally complete security master**. No live identities
or mappings are seeded by migration. Unknown listing periods stay null. Current
alias resolution can return a supported identity without claiming historical dates;
a dated query with unknown effective start remains ambiguous. Overlapping mappings
are retained as conflicting evidence and surfaced for human review.

Unmapped ticker resolution returns `{status: legacy_symbol, identity_id: null}`.
This is compatibility, not a fabricated permanent identity. Existing research and
prediction services are not destructively migrated. Source/identity definitions
cannot be edited in place; incompatible definitions require explicit new identity
scope or a separately designed future versioning process.

## Raw evidence, events and revisions

`PITStore.raw` stores bytes before normalization using the repository's atomic,
content-addressed immutable artifact helper under `pit-raw/`. It records source,
resource/request identity, checksum/path, observed time, parser version and optional
provider/HTTP metadata. Repeating an identical retrieval identity deduplicates its
row; observations at different times keep separate rows but share identical bytes.
Changed bytes get a new object. Failed parsing retains raw evidence. HTTP metadata
must be sanitized by future collectors; do not store API keys or authorization
headers in resources/metadata.

An event identifies the fact. A revision identifies a version with raw provenance,
payload fingerprint, source record ID, event/source/availability times, observation,
ingestion, parser version and optional supersedes/amendment relationship. Revisions
cannot overwrite history. Supersession must stay within the event and cannot move
known availability/observation backwards. SEC amendment filings are separate events
with explicit evidence-backed `amends_event` relations, not replacements.

Canonical serialization and SHA-256 reuse existing utilities. Ingestion/creation
clock samples are outside semantic fingerprints; raw observation times remain part
of retrieval/revision evidence. SQL FKs, uniqueness, immutability triggers, identity
parent constraints, temporal checks and as-of indexes strengthen invariants.
An artifact and SQLite do not share one transaction: orphan bytes can survive a
crash and are exposed by artifact audit, not deleted automatically.

## Offline SEC contract

The minimal single-filing adapter preserves CIK, accession, form, filing date,
acceptance datetime, period of report and primary document. It requires an explicit,
unambiguous pre-existing CIK/entity mapping. Raw bytes are saved first. Acceptance
is `source_time`; using it for availability produces `proxy` with basis
`sec_acceptance_proxy` and policy `sec_acceptance_proxy_v1`. Missing acceptance
produces unknown historical availability, not a guessed publication time.

An `/A` form remains a separate filing. Original linkage requires explicit original
event ID plus evidence and compatible entity/form family. No nearest-date matching.
Without proof, lineage is `unresolved` and enters audit/reconciliation findings.

The minimal XBRL fact adapter distinguishes entity, taxonomy, concept, unit,
start/end or instant, and dimensions in logical identity. Accession/filed date,
value and raw evidence remain revision provenance. A filed date alone does not
prove exact fact availability, so representative companyfacts inputs remain
`unknown` unless a future supported contract supplies stronger evidence. This is
not a full companyfacts normalizer or dimensional XBRL engine.

Official semantics checked on 2026-09-23:

- Public data.sec.gov submissions/XBRL APIs require no API key. SEC describes
  processing delays, so API visibility cannot be equated with a filer timestamp.
  [SEC API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- Automated access must declare a User-Agent and stay at or below 10 requests per
  second. SEC does not guarantee the lag from acceptance to public visibility, nor
  the accuracy/scope of ticker/CIK associations.
  [SEC developer FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions)

These are future collector constraints; Phase 2A contains no HTTP client.

## Offline ALFRED contract

The observation contract keeps `series_id`, `observation_date`, decimal/missing
value, `realtime_start`, `realtime_end`, requested `vintage_date`, observation and
ingestion separately. Series+observation date defines a logical event; successive
vintages remain revisions. Missing `.` is preserved, not changed to zero. A caller
must supply the date-policy timezone explicitly; it is not asserted to be an exact
provider timestamp. In the fixtures, February vintage 100 remains visible before
the March revision to 103, under the explicitly chosen date-level policy.

FRED documents realtime periods as inclusive start/end dates describing what was
known historically; default endpoints often use today's realtime period. The
foundation requires explicit cutoff instead of inheriting that default.
[Official realtime semantics](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html)

The observation endpoint supports realtime and vintage parameters. This phase
accepts a minimal normalized observation, not an unbounded provider response.
[Observation API](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)

Published release dates do not necessarily establish when data appeared on the
FRED/ALFRED websites; no intraday timestamp is manufactured from release dates.
[Release date documentation](https://fred.stlouisfed.org/docs/api/fred/release_dates.html)

## Experiment, integrity and reconciliation integration

`input_manifest` freezes query policy/version, cutoff, explicit identity/source
filters, selected revision IDs/fingerprints, raw IDs/checksums/fingerprints and
availability basis. Ambiguous queries cannot become manifests.
`with_pit_inputs` adds this under `ExperimentSpec.contract.data.pit_manifest` in a
**new** specification. Registry validation rejects invalid/unverifiable manifests;
new runs must carry the same `provenance.pit_manifest`. Existing specs/runs never
change. Built-in Yahoo candidate execution does not consume PIT inputs and does
not silently accept a changed contract; a future consumer must explicitly implement
that data path before any training is authorized.

The Phase 1.5 manifest naturally references this frozen spec/run evidence. Metadata
review follows revision → raw record → fingerprints → cutoff/policy, without provider
calls. Artifact review also checks raw bytes. PIT policy warnings map to evidence
`unverifiable`, preserving the stronger exact-proof distinction; leakage `invalid`
maps to mismatch. Registered frame input checks still apply independently.

Leakage status is `safe`, `warning`, `invalid` or `unverifiable`. Findings include
future/disallowed inputs, observed-only passed off as historical proof, proxies in
strict mode, missing query metadata, changed fingerprints and selecting a different
revision from the historical query. The validator reports discrepancies; it never
repairs experiments. Newly added conflicting historical source evidence can make a
previous frozen selection require review; original manifests stay unchanged.

PIT audit issues become `pit_*` cases through existing `integrity reconcile detect`.
Unknown mappings, conflicting amendments, impossible timestamps, missing bytes and
checksum problems are not automatically resolved. Existing review/evidence/dismissal
actions remain available; no new action mutates immutable identities or revisions.

## Operational boundary

`operational_handler(pit, parser)` adapts a future explicitly registered Phase 0 job
that already has pinned raw evidence. It verifies the raw checksum, crosses the
existing effect fence, calls a typed offline parser, and returns revision IDs.
Partial failures raise the existing `UnsafeExecution` condition for blocked review.
It registers/schedules nothing. Future provider fetch, retry/rate-limit/freshness
policy belongs in the existing Phase 0 runner, never a second scheduler.

## CLI and read-only API

All outputs are JSON. Put `--database PATH` after `data` to inspect another database.

```bash
python -m stock_app.research data sources
python -m stock_app.research data resolve AAPL
python -m stock_app.research data resolve TEST --scope XNAS --on-date 2025-02-01
python -m stock_app.research data security ID
python -m stock_app.research data identifiers ID
python -m stock_app.research data history EVENT_ID
python -m stock_app.research data as-of --time 2025-02-15T00:00:00Z --strictness strict
python -m stock_app.research data as-of --time 2025-02-15T00:00:00Z --strictness allow_proxy --data-type macro_observation
python -m stock_app.research data leakage-check manifest.json
python -m stock_app.research data audit --depth metadata
python -m stock_app.research data audit --depth artifact --time 2025-02-15T00:00:00Z
```

GET-only APIs under `/api/research/pit`: `/sources`, `/resolve/<value>`,
`/identities/<id>` (with alias history), `/events/<id>` (with revision history),
`/as-of?as_of=...&strictness=...` and `/audit?depth=metadata|artifact`.
Queries support `identity_id`, `source_id`, `data_type`; resolve supports `namespace`,
`scope`, `on_date`. No arbitrary mutation/provider-ingestion HTTP endpoints exist.

Audit reports table counts, fingerprint/identity/FK defects, source duplicates,
missing/checksum-invalid raw evidence, temporal and lineage conflicts, overlapping
or uncertain aliases, availability kinds, and optional future-known revision counts
relative to an explicit cutoff. Future revisions are valid stored history; the count
is not itself a leakage error. Default audit never contacts a provider or hashes
files; artifact mode additionally verifies bytes/orphans. Phase 2B adds indexed primary-ID/identifier reads and event-specific revision retrieval,
plus page checkpoints. Unfiltered audit/coverage still scan the pilot universe; large
backfills require further measured query-plan and capacity review.

## Migration, fixtures and limitations

`python -m stock_app.research.pit.migration DATABASE` acquires the shared worker
lock, backs up the database, inventories **all pre-existing tables including Phase
1.5**, active bindings, canonical records and model bytes, then installs six additive
tables and verifies original hashes/counts. It is restart-safe and never converts
old history. Source identities/security mappings are intentionally empty in the
working database until explicitly supported by evidence.

`tests/fixtures/pit` contains only small, documented synthetic examples. Tests are
offline, use injected clocks, never sleep, and do not imply live provider validation.
The existing complete test suite still performs its isolated estimator fixtures;
no working model is retrained. Local owner-controlled files/SQLite are not tamper-
proof, source assertions need provenance review, and no commercial security-master
coverage is claimed. Identifier corrections require new explicit evidence and
review rather than guessing/deleting prior aliases. No automated retention/deletion
or provider authentication/transport implementation is added.

Phase 2B is implemented as a separately controlled [provider ingestion pilot](pit-provider-ingestion.md).
It retains the Phase 2A contracts, adds secure transport and bounded checkpointed collection,
and leaves provider refresh disabled by default. Live validation was not executed because
SEC/FRED configuration was unavailable; no live identities or data were seeded. See the
[Phase 2B verification report](pit-provider-verification.md). Phase 2C has not begun.
