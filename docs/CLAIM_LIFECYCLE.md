# Claim Lifecycle

## Required state transition

```text
raw source record
-> imported claim
-> candidate or review state
-> resolved claim
-> derived value when applicable
-> editorial selection
-> published statement
-> immutable publication manifest
-> correction creates a new version
```

Source releases and final publication records are append-only in this phase:
later work may supersede them but cannot erase a release or published artifact.
Imported claims are tied to an immutable release and use explicit lifecycle
states, but the schema does not make every claim row immutable. Resolutions and
derivations are version-capable working products. Publication captures each
statement's complete evidence root as canonical JSON with its own SHA-256 hash.
The manifest source-snapshot hash is derived from those ordered hashes. Later
working-graph edits therefore cannot alter the evidence representation retained
for an earlier published statement.

## Import and review

Acquisition first creates an immutable `source_release` with raw checksum and
artifact/record location. Every imported `claim` has that release, a raw-record
locator, assertion body, temporal qualification, data state, and optional
missing reason. Import is not acceptance.

| `claim_assertion_status` | Meaning |
| --- | --- |
| `imported` | Captured from a source release before triage. |
| `candidate` | Imported and awaiting assessment. |
| `in_review` | Being assessed for evidence, quality, scope, or conflict. |
| `accepted` | Accepted as evidence for a resolution, subject to later supersession. |
| `rejected` | Retained but not accepted for the asserted conclusion. |
| `superseded` | Retained historical assertion replaced by a later claim/resolution. |
| `retracted` | Retained assertion removed by its source or invalidated with a reason. |

The normal transition is `imported -> candidate -> in_review -> accepted` or
`rejected`. Accepted material can become `superseded` or `retracted`. A `review_task`
records workflow rather than hiding it in mutable editorial prose.

## Resolution and conflict

A `resolved_claim` is a canonical assertion with a documented
`resolution_method`: `single_source`, `corroborated`, `editorial_review`, or
`methodological_derivation`. Period allocation is represented separately by
`temporal_assignment`. A resolved claim retains
`resolved_claim_evidence` links to imported claims, each with an
`evidence_position` of `supporting` or `dissenting`; at least one supporting
link is required, while dissent is preserved alongside it.

Disagreement is not silently collapsed. The resolver records stance, rationale,
source quality, and unresolved ambiguity. A conflict may produce an uncertainty
note or remain unresolved rather than force a consensus. Supersession creates a
new record linked to the prior record; the old record remains auditably intact.

## Time and missingness

`temporal_precision` is one of `day`, `month`, `year`, `decade`, `interval`, or
`unknown`. `temporal_assignment` is one of `direct_record`, `reported`,
`inferred`, `uniform_period_allocation`, `modeled_period_allocation`,
`editorial_context`, or `unknown`. `date_role` is one of `occurred`, `began`,
`ended`, `reported`, `discovered`, `published`, `predicted`, or `commemorated`.
These are independent: an annual total can have year precision and be uniformly
allocated to a selected day.

`data_status` is `reported`, `estimated`, `modeled`, `provisional`, `final`,
`missing`, or `withdrawn`. When it is `missing`, `missing_reason` is required
and is one of `not_collected`, `not_available`, `not_applicable`, `withheld`,
`invalid`, or `unknown`. Numeric zero is valid only as a present numeric value;
null/missing may never be serialized, defaulted, or calculated as zero.

## Derivation and editorial selection

A `derived_value` requires a versioned `methodology` plus either direct
`provenance_resolved_claim_id` evidence or durable `derived_value_inputs` links
to eligible observations or resolved claims. It records time qualification,
data state, unit, parameters, input hash, and `comparability_status`: `comparable`,
`partially_comparable`, `not_comparable`, or `unknown`.

A daily equivalent is therefore a documented allocation, not an observation.
Editorial selection chooses resolved claims and eligible values for a profile;
it cannot alter evidence or introduce a provenance-free statement.

## Publication and correction

Profile construction writes JSON, computes a content hash, then creates a
manifest, immutable statement-evidence rows with canonical evidence snapshots,
and a profile version. Every JSON statement path must map to exactly one
resolved claim or derived value before the manifest can be published. Snapshot
construction must find every referenced claim, release, methodology, and
derived input, including transitive metric definitions, geography versions,
quality assessments, pipeline runs, and source lineage, or publication fails.
`publication_status` is `draft`, `published`,
`superseded`, or `withdrawn`; it is distinct from data status. A day profile
references a publication manifest, and a published manifest is immutable.

A correction records its trigger, rationale, and prior/replacement relationships.
It creates new claims, resolutions, values, JSON, manifests, and day-profile
versions as needed. It never overwrites published bytes or a manifest hash. A
corrected profile supersedes or withdraws the prior artifact while retaining a
navigable, linear provenance path; a predecessor has at most one direct
successor.

## USGS Slice Application

For `official19640328033616_30`, one immutable release produces separate candidate claims for identity, type, title, UTC occurrence, Alaska local civil date, coordinates, named geography, magnitude, and depth. Each claim carries the release, public record locator, record SHA-256, temporal precision/assignment, unit, and numeric bounds when applicable.

Ingestion creates open review tasks. Editorial acceptance changes candidates to accepted, closes tasks as resolved, and creates versioned resolved claims using the `single_source` method. The rationale explicitly records that official-source acceptance is not independent corroboration. A changed resolution supersedes rather than overwrites its predecessor. Dissent is retained in `resolved_claim_evidence`; dependent copies share a lineage root and count once in deterministic agreement logic.

Publication snapshots the evidence chain before the manifest becomes published. Profile v1 is never rewritten. A subsequent publication must create a new manifest and `/day/{date}/profile-v{n}.json` object linked to both predecessor manifest and day profile.
