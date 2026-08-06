import {
  DAY_PROFILE_SECTION_KEYS,
  PUBLICATION_TIERS,
  profileTypeForDate,
  type DayProfileSectionKey,
  type ProfileNotPublished,
  type ProfileStatement,
  type ProfileStatementEventGroup,
  type PublishedProfileResponse
} from "@day-perspective/contracts";

import { isSupportedPublicDate } from "./date";

const SECTION_TITLES: Record<DayProfileSectionKey, string> = {
  recorded_on_this_date: "Recorded on this date",
  typical_day_in_this_year: "Typical day in this year",
  wider_historical_context: "Wider historical context",
  curated_claims: "Curated claims",
  derived_comparisons: "Derived comparisons",
  wonder_and_progress: "Wonder and progress",
  evidence_notes: "Evidence notes"
};

export const DAY_PROFILE_SECTIONS = DAY_PROFILE_SECTION_KEYS.map((key) => ({
  id: key.replaceAll("_", "-"),
  key,
  title: SECTION_TITLES[key]
}));

const ERA_LINES: Record<ReturnType<typeof profileTypeForDate> & string, string> = {
  limited_historical: "Limited historical era · 1900–1949",
  standard_statistical: "Standard statistical era · 1950–1988",
  enhanced_structured: "Enhanced structured era · 1989–2025"
};

/**
 * Era bands for the landing horizon (UI_UX_CONTRACT C-3.1 vocabulary,
 * Strata direction). Boundaries mirror profileTypeForDate; the tiling
 * property is unit-tested against it.
 */
export const ERA_BANDS = [
  {
    key: "limited_historical" as const,
    line: ERA_LINES.limited_historical,
    start: "1900-01-01",
    end: "1949-12-31"
  },
  {
    key: "standard_statistical" as const,
    line: ERA_LINES.standard_statistical,
    start: "1950-01-01",
    end: "1988-12-31"
  },
  {
    key: "enhanced_structured" as const,
    line: ERA_LINES.enhanced_structured,
    start: "1989-01-01",
    end: "2025-12-31"
  }
];

/**
 * Canonical era line per UI_UX_CONTRACT C-3.1; null outside the shell and
 * for non-calendar values (profileTypeForDate alone is lexical and would
 * assign an era to impossible dates like 1964-02-30).
 */
export function eraLineForDate(value: string): string | null {
  if (!isSupportedPublicDate(value)) {
    return null;
  }
  const profileType = profileTypeForDate(value);
  return profileType ? ERA_LINES[profileType] : null;
}

export type DayProfileSection = (typeof DAY_PROFILE_SECTIONS)[number];

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
}

function isClaimProvenance(value: unknown): boolean {
  const claim = asRecord(value);
  return (
    claim !== undefined &&
    typeof claim.predicate === "string" &&
    (claim.value === null || asRecord(claim.value) !== undefined) &&
    typeof claim.source_record_locator === "string" &&
    typeof claim.source_record_hash_sha256 === "string"
  );
}

function isStatementProvenance(value: unknown): boolean {
  const provenance = asRecord(value);
  if (
    provenance === undefined ||
    typeof provenance.published_statement !== "string" ||
    !Array.isArray(provenance.supporting_claims) ||
    !provenance.supporting_claims.every(isClaimProvenance) ||
    !Array.isArray(provenance.dissenting_claims) ||
    !provenance.dissenting_claims.every(isClaimProvenance)
  ) {
    return false;
  }
  const sourceRelease = asRecord(provenance.source_release);
  const methodology = asRecord(provenance.methodology);
  if (
    sourceRelease === undefined ||
    typeof sourceRelease.source !== "string" ||
    !(
      sourceRelease.publisher === null ||
      typeof sourceRelease.publisher === "string"
    ) ||
    typeof sourceRelease.release !== "string" ||
    typeof sourceRelease.source_url !== "string" ||
    typeof sourceRelease.raw_checksum_sha256 !== "string" ||
    typeof sourceRelease.retrieved_at !== "string" ||
    methodology === undefined ||
    typeof methodology.name !== "string" ||
    typeof methodology.version !== "string" ||
    typeof methodology.description !== "string"
  ) {
    return false;
  }
  const resolved = provenance.resolved_claim;
  if (resolved !== undefined) {
    const row = asRecord(resolved);
    if (
      row === undefined ||
      typeof row.canonical_key !== "string" ||
      typeof row.version !== "number" ||
      typeof row.method !== "string" ||
      typeof row.rationale !== "string"
    ) {
      return false;
    }
  }
  const derived = provenance.derived_value;
  if (derived !== undefined) {
    const row = asRecord(derived);
    if (
      row === undefined ||
      typeof row.kind !== "string" ||
      typeof row.calculation_version !== "string" ||
      !(row.value === null || asRecord(row.value) !== undefined)
    ) {
      return false;
    }
  }
  return resolved !== undefined || derived !== undefined;
}

/**
 * A position in a sequence: a whole number, not negative.
 *
 * `Number.isFinite` alone admits `-1` and `1.5`, which sort perfectly well —
 * that is the hazard. Nothing throws; the wrong statement quietly takes the
 * lead treatment, which is the one position on the page a reader reads as the
 * page's claim about what the date is.
 */
function isOrdinal(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

/**
 * Read a complete event group out of a value, or null if it is not one.
 *
 * The single definition of what a usable group is, used by both the response
 * boundary and the renderer. They previously carried a copy each and drifted:
 * the renderer rejected an empty `event_group_key` while the boundary accepted
 * it, so one empty string passed validation and then silently removed every
 * event boundary on the page instead of raising the API-error state.
 *
 * An empty key or title is not a usable group. A key that identifies nothing
 * cannot group statements, and a title that names nothing cannot head a group
 * for a reader — the two failure modes this metadata exists to prevent.
 */
export function readEventGroup(
  value: unknown
): ProfileStatementEventGroup | null {
  const group = asRecord(value);
  if (
    group === undefined ||
    typeof group.event_group_key !== "string" ||
    group.event_group_key === "" ||
    typeof group.event_title !== "string" ||
    group.event_title === "" ||
    typeof group.featured !== "boolean" ||
    !isOrdinal(group.event_order) ||
    !isOrdinal(group.predicate_order)
  ) {
    return null;
  }
  return group as unknown as ProfileStatementEventGroup;
}

function isProfileStatement(value: unknown): value is ProfileStatement {
  const statement = asRecord(value);
  return (
    statement !== undefined &&
    typeof statement.statement_id === "string" &&
    typeof statement.statement === "string" &&
    (statement.event_group === undefined ||
      readEventGroup(statement.event_group) !== null) &&
    (statement.provenance_note === undefined || typeof statement.provenance_note === "string") &&
    (statement.details === undefined || asRecord(statement.details) !== undefined) &&
    (statement.provenance === undefined ||
      isStatementProvenance(statement.provenance))
  );
}

function isSectionKey(value: string): value is DayProfileSectionKey {
  return (DAY_PROFILE_SECTION_KEYS as readonly string[]).includes(value);
}

function isSectionStates(value: unknown): boolean {
  if (value === undefined) {
    return true;
  }
  const states = asRecord(value);
  return (
    states !== undefined &&
    Object.entries(states).every(([key, value]) => {
      const state = asRecord(value);
      return (
        isSectionKey(key) &&
        state !== undefined &&
        (state.status === "available" || state.status === "not_yet_supported") &&
        (state.reason === undefined || typeof state.reason === "string")
      );
    })
  );
}

export function isProfileNotPublished(payload: unknown, expectedDate: string): payload is ProfileNotPublished {
  const response = asRecord(payload);
  const expectedProfileType = profileTypeForDate(expectedDate);
  return (
    response !== undefined &&
    response.status === "profile_not_published" &&
    response.date === expectedDate &&
    response.profile_type === expectedProfileType &&
    typeof response.detail === "string"
  );
}

export function isPublishedProfileResponse(
  payload: unknown,
  expectedDate: string
): payload is PublishedProfileResponse {
  const response = asRecord(payload);
  const expectedProfileType = profileTypeForDate(expectedDate);
  if (
    response === undefined ||
    response.status !== "published" ||
    response.date !== expectedDate ||
    response.profile_type !== expectedProfileType ||
    typeof response.manifest_id !== "string" ||
    typeof response.content_hash !== "string"
  ) {
    return false;
  }

  const profile = asRecord(response.profile);
  if (
    profile === undefined ||
    profile.schema_version !== "1" ||
    profile.date !== expectedDate ||
    profile.profile_type !== expectedProfileType
  ) {
    return false;
  }

  if (
    profile.publication_tier !== undefined &&
    !(PUBLICATION_TIERS as readonly string[]).includes(
      profile.publication_tier as string
    )
  ) {
    return false;
  }

  if (profile.quality !== undefined) {
    const quality = asRecord(profile.quality);
    if (
      quality === undefined ||
      typeof quality.grade !== "string" ||
      typeof quality.explanation !== "string"
    ) {
      return false;
    }
  }

  if (profile.source_attribution !== undefined) {
    const attribution = asRecord(profile.source_attribution);
    if (
      attribution === undefined ||
      typeof attribution.name !== "string" ||
      typeof attribution.publisher !== "string" ||
      typeof attribution.url !== "string"
    ) {
      return false;
    }
  }

  // An attribution the page renders as a credit is a claim about who stands
  // behind the content, so a malformed entry must fail the profile rather than
  // reach a reader as a broken credit.
  //
  // The two sides are not symmetric, and deliberately so. `Source.name` is
  // non-nullable upstream, so an empty name means a corrupt payload and is
  // rejected. `publisher` and `canonical_url` are both nullable, and the
  // publisher emits "" for an absent one — that is unknown data, not corrupt
  // data, and rejecting the whole profile over a source with no recorded URL
  // would throw away a page of honest evidence. The renderer names such a
  // source without linking or crediting it.
  if (profile.source_attributions !== undefined) {
    if (!Array.isArray(profile.source_attributions)) {
      return false;
    }
    for (const entry of profile.source_attributions) {
      const attribution = asRecord(entry);
      if (
        attribution === undefined ||
        typeof attribution.name !== "string" ||
        attribution.name === "" ||
        typeof attribution.publisher !== "string" ||
        typeof attribution.url !== "string"
      ) {
        return false;
      }
    }
  }

  const sections = asRecord(profile.sections);
  return (
    sections !== undefined &&
    isSectionStates(profile.section_states) &&
    Object.entries(sections).every(
      ([key, statements]) =>
        isSectionKey(key) &&
        Array.isArray(statements) &&
        statements.every((statement) => isProfileStatement(statement))
    ) &&
    hasCoherentEventGroups(sections.recorded_on_this_date)
  );
}

/**
 * Exactly one event leads, and the group keys agree with themselves.
 *
 * Per-statement validation cannot see this: two groups can each be perfectly
 * well formed and both claim `featured`. The renderer would then give both lead
 * treatment, and the page shows two headlines with nothing telling a reader
 * which event the date is actually about — the single question D046 exists to
 * answer.
 *
 * Checked only among statements that declare a group. A section carrying none
 * is a profile published before typed grouping; it renders flat and there is no
 * invariant to check.
 */
function hasCoherentEventGroups(statements: unknown): boolean {
  if (!Array.isArray(statements)) {
    return true;
  }
  const groups = new Map<string, ProfileStatementEventGroup>();
  let declared = 0;
  for (const statement of statements) {
    const record = asRecord(statement);
    if (record === undefined || record.event_group === undefined) {
      continue;
    }
    declared += 1;
    const group = readEventGroup(record.event_group);
    if (group === null) {
      return false;
    }
    const seen = groups.get(group.event_group_key);
    if (seen === undefined) {
      groups.set(group.event_group_key, group);
      continue;
    }
    // One key must describe one event, or grouping by it means nothing.
    if (
      seen.event_title !== group.event_title ||
      seen.featured !== group.featured ||
      seen.event_order !== group.event_order
    ) {
      return false;
    }
  }
  if (groups.size === 0) {
    return true;
  }
  // Contiguity is checked only when grouping will actually be used to render.
  // A partially grouped section falls back to flat, so its sequence is never
  // read, and failing the profile there would replace a readable page with an
  // error over an ordering nothing consults.
  if (declared === statements.length && !hasContiguousPredicates(statements)) {
    return false;
  }
  // Requiring a featured group applies only to a fully grouped section. The
  // featured event's own statements can legitimately be ungrouped —
  // `events_by_source_release` omits a release that produced two events on one
  // date rather than guessing — leaving a section whose only declared group is
  // a secondary one. That renders flat, which is readable and honest; failing
  // the profile would turn a safe degradation into an error page.
  const featured = [...groups.values()].filter((group) => group.featured);
  if (declared === statements.length && featured.length !== 1) {
    return false;
  }
  // Distinct groups must not share an `event_order`. A tie falls through to the
  // opaque-key comparator, so a duplicate does not fail — it renders the events
  // in hash order, which is the one ordering the contract forbids. Uniqueness
  // is a different property from contiguity: gaps stay allowed, ties do not.
  const orders = [...groups.values()].map((group) => group.event_order);
  if (new Set(orders).size !== orders.length) {
    return false;
  }
  // `event_order` is deliberately NOT required to be contiguous across groups,
  // even though the publisher assigns it with `enumerate`. It enumerates the
  // *admitted events*, while a group exists only for an event that contributed
  // an attributable statement (`wikidata.py:1559-1562`) — so an admitted event
  // whose statements could not be attributed leaves a real gap in an otherwise
  // honest payload. Requiring 0..m-1 here would reject it. Only the property
  // the publisher actually guarantees is checked: the featured group is 0.
  // The headline is also the one the payload orders first, so a renderer
  // sorting by either field reaches the same event. Gated with the other
  // cross-group invariants (C-3.5.1): a partially grouped section renders
  // flat, so nothing reads the correlation, and enforcing it there rejects a
  // payload the contract calls valid.
  if (declared !== statements.length) {
    return true;
  }
  return [...groups.values()].every(
    (group) => group.featured === (group.event_order === 0)
  );
}

/**
 * Each group's `predicate_order` runs 0..n-1 over its own statements.
 *
 * Per-group, not per-section: two events each starting at 0 is correct, which
 * is the whole point of the field — a group stays ordered when rendered alone,
 * collapsed, or reordered. A gap or a duplicate means the payload's stated
 * order is not an order, and the renderer would sort it into something
 * plausible rather than something true.
 */
function hasContiguousPredicates(statements: unknown[]): boolean {
  const byGroup = new Map<string, number[]>();
  for (const statement of statements) {
    const record = asRecord(statement);
    const group = record && readEventGroup(record.event_group);
    if (!group) {
      return false;
    }
    const orders = byGroup.get(group.event_group_key) ?? [];
    orders.push(group.predicate_order);
    byGroup.set(group.event_group_key, orders);
  }
  for (const orders of byGroup.values()) {
    const sorted = [...orders].sort((left, right) => left - right);
    if (sorted.some((order, index) => order !== index)) {
      return false;
    }
  }
  return true;
}
