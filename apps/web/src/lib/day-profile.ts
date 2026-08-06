import {
  DAY_PROFILE_SECTION_KEYS,
  PUBLICATION_TIERS,
  profileTypeForDate,
  type DayProfileSectionKey,
  type ProfileNotPublished,
  type ProfileStatement,
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
 * A statement's event group is either absent or complete.
 *
 * Absent is normal — profiles published before typed grouping carry no group,
 * and the section renders flat. A present-but-malformed group is a payload that
 * does not match the contract, and it is rejected here for the same reason a
 * malformed `provenance` is: the boundary is where a wrong shape becomes an
 * API error rather than something the render path has to survive.
 */
function isEventGroup(value: unknown): boolean {
  const group = asRecord(value);
  return (
    group !== undefined &&
    typeof group.event_group_key === "string" &&
    typeof group.event_title === "string" &&
    typeof group.featured === "boolean" &&
    typeof group.event_order === "number" &&
    Number.isFinite(group.event_order) &&
    typeof group.predicate_order === "number" &&
    Number.isFinite(group.predicate_order)
  );
}

function isProfileStatement(value: unknown): value is ProfileStatement {
  const statement = asRecord(value);
  return (
    statement !== undefined &&
    typeof statement.statement_id === "string" &&
    typeof statement.statement === "string" &&
    (statement.event_group === undefined || isEventGroup(statement.event_group)) &&
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

  // Validated with the same strictness as the singular field it replaces. An
  // attribution the page renders as a source link is a claim about who stands
  // behind the content, so a malformed entry must fail the profile rather than
  // reach a reader as a broken or empty credit.
  if (profile.source_attributions !== undefined) {
    if (!Array.isArray(profile.source_attributions)) {
      return false;
    }
    for (const entry of profile.source_attributions) {
      const attribution = asRecord(entry);
      if (
        attribution === undefined ||
        typeof attribution.name !== "string" ||
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
    )
  );
}
