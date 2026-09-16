# Product Contract

## Purpose

Day Perspective is an evidence-resolution and historical-comparison system
organized around a calendar date. It is not an "on this day" trivia app.
Events, observations, impacts, scores, and day profiles are products of claims;
importing a source record never makes it accepted truth.

Every published statement must be traceable:

```text
published statement
-> immutable publication statement evidence
-> resolved claim or derived value
-> imported claim or input observation
-> immutable source release
-> raw source record or file
-> methodology and code version
-> immutable publication manifest
```

## Public date shell and profile bands

The public date shell accepts valid ISO Gregorian dates from `1900-01-01`
through `2025-12-31`, inclusive. A supported date may still be unpublished.

| Date range | Profile type | Contract |
| --- | --- | --- |
| 1900-1949 | `limited_historical` | Limited historical profile. Direct records and structured statistics may be sparse or uneven. The product exposes gaps rather than inventing coverage. |
| 1950-1988 | `standard_statistical` | Standard statistical profile. Period statistics may support labeled daily equivalents and comparisons when coverage permits. |
| 1989-2025 | `enhanced_structured` | Enhanced structured profile. More structured evidence may be shown where source releases support it. |

The bands describe evidence shape, not historical importance or a guarantee of
content. No band permits unprovenanced claims or synthetic filler.

## Public content contract

The interface must keep these categories visibly separate.

| Section | What it is | What it is not |
| --- | --- | --- |
| Recorded on this date | A resolved event associated with the selected date and explicit date role, precision, and assignment. | A period statistic or unreviewed source assertion. |
| Typical day in this year | A methodology-bound daily equivalent derived from a stated period total, denominator, coverage, and inputs. | A date-specific observation. |
| Wider historical context | A resolved condition assigned to a surrounding period and labeled as direct, inferred, or period-allocated. | Proof that the condition occurred on the selected day. |
| Curated claims | Resolved, evidence-backed claims selected by an editorial rule. | Automated ranking or a complete history. |
| Derived comparisons | Methodology-bound comparison with an explicit comparability status. | A fact when evidence is not comparable. |
| Wonder and progress | Evidence-backed discoveries, cultural milestones, and human progress where editorially selected. | A universal progress or hardship score. |
| Evidence notes | Provenance, source quality, disagreement, missingness, methods, and corrections. | Optional fine print. |

Curated apocalypse predictions may appear only as source-backed records of a
prediction, its stated deadline, and its outcome status when that deadline had
already passed on the selected date. They are neither endorsements nor event
claims.

## Evidence, uncertainty, and comparison rules

A directly recorded event must be supported by a resolved claim and display its
temporal precision, temporal assignment, and date role. A reporting or
publication date is not automatically an occurrence date.

An event is filed under the conventional local civil day at its place of
occurrence.

A source that states a calendar day states it in some convention, and the adapter
for that source must establish which. A convention fixes two separate things: the
calendar system that names the day, and the meridian at which the day begins.
They do not behave alike.

A day already stated as the conventional local civil day is taken as reported and
is never re-derived. Where only the calendar system differs — a Julian civil date
from a jurisdiction that had not yet adopted the Gregorian calendar, for instance
— the day is restated on the product's Gregorian axis, and the published record
carries the source's calendar system and the fact that the day was restated. That
correspondence is exact: it renames the same local civil day rather than choosing
between days, so it invents no precision.

A day whose meridian differs — a UTC calendar day, for instance — denotes a
twenty-four hour interval that at any nonzero local offset falls across two local
civil days. It does not by itself place the event on either, and choosing one
would invent precision the source never stated. Such a day yields a date-specific
event only where further evidence — an instant, or the source's own statement of
the local day — resolves the interval to a single local day.

A source whose convention is not established does not yield a date-specific
event.

A source that states an instant has not stated a date. The day is derived, and
the published record carries the timezone applied, the resulting offset, and the
fact that the day was derived rather than reported. An instant whose place of
occurrence is unknown is not published as a date-specific event, because the
product does not assign a day by choosing a meridian.

A daily equivalent is a derived value. It shows its source period, allocation
method, denominator, coverage, methodology, and comparability status. Missing
values remain missing and are never converted to zero.

Editorial selection follows resolution; it cannot bypass source releases,
evidence links, methodology, quality assessment, or dissent. Comparisons are
labeled `comparable`, `partially_comparable`, `not_comparable`, or
`unknown`.

Each published artifact must expose enough provenance to identify source
release, raw record locator where permitted, claim/input, data state, temporal
qualification, methodology version, quality assessment where available, and
manifest content hash. It must make supporting and dissenting evidence,
lineage, missingness, allocation, disagreement, supersession, and corrections
inspectable. Each statement path in a published profile has one immutable
database mapping to either a resolved claim or a derived value; it cannot be
published as an opaque JSON sentence without that mapping.

## Explicit deferrals

This phase does not implement GDELT, EM-DAT, Wikidata, UCDP, UN demographic,
or full-source ingestion; a universal hardship score; full event-ranking
automation; accounts; comments; social features; user-submitted claims; ancient
history; AI-generated historical facts; live runtime source queries; production
deployment; all 46,000 profiles; a message queue; a vector database; or a
graph database.

Fixtures are test/development-only and are never production historical facts.
