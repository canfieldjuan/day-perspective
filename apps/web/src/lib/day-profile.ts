import {
  DAY_PROFILE_SECTION_KEYS,
  profileTypeForDate,
  type DayProfileSectionKey,
  type ProfileNotPublished,
  type ProfileStatement,
  type PublishedProfileResponse
} from "@day-perspective/contracts";

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

export type DayProfileSection = (typeof DAY_PROFILE_SECTIONS)[number];

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
}

function isClaimEvidence(value: unknown): boolean {
  const claim = asRecord(value);
  return (
    claim !== undefined &&
    typeof claim.predicate === "string" &&
    asRecord(claim.value) !== undefined &&
    typeof claim.source_record_locator === "string" &&
    typeof claim.source_record_hash_sha256 === "string"
  );
}

function isProvenance(value: unknown): boolean {
  const provenance = asRecord(value);
  if (provenance === undefined || typeof provenance.published_statement !== "string") {
    return false;
  }
  const resolvedClaim = asRecord(provenance.resolved_claim);
  const sourceRelease = asRecord(provenance.source_release);
  const methodology = asRecord(provenance.methodology);
  return (
    resolvedClaim !== undefined &&
    typeof resolvedClaim.canonical_key === "string" &&
    typeof resolvedClaim.version === "number" &&
    typeof resolvedClaim.method === "string" &&
    typeof resolvedClaim.rationale === "string" &&
    Array.isArray(provenance.supporting_claims) &&
    provenance.supporting_claims.every(isClaimEvidence) &&
    Array.isArray(provenance.dissenting_claims) &&
    provenance.dissenting_claims.every(isClaimEvidence) &&
    sourceRelease !== undefined &&
    typeof sourceRelease.source === "string" &&
    typeof sourceRelease.publisher === "string" &&
    typeof sourceRelease.release === "string" &&
    typeof sourceRelease.source_url === "string" &&
    typeof sourceRelease.raw_checksum_sha256 === "string" &&
    typeof sourceRelease.retrieved_at === "string" &&
    methodology !== undefined &&
    typeof methodology.name === "string" &&
    typeof methodology.version === "string" &&
    typeof methodology.description === "string"
  );
}

function isProfileStatement(value: unknown): value is ProfileStatement {
  const statement = asRecord(value);
  return (
    statement !== undefined &&
    typeof statement.statement_id === "string" &&
    typeof statement.statement === "string" &&
    (statement.provenance_note === undefined || typeof statement.provenance_note === "string") &&
    (statement.details === undefined || asRecord(statement.details) !== undefined) &&
    (statement.provenance === undefined || isProvenance(statement.provenance))
  );
}

function isSectionKey(value: string): value is DayProfileSectionKey {
  return (DAY_PROFILE_SECTION_KEYS as readonly string[]).includes(value);
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

  const sections = asRecord(profile.sections);
  return (
    sections !== undefined &&
    Object.entries(sections).every(
      ([key, statements]) =>
        isSectionKey(key) &&
        Array.isArray(statements) &&
        statements.every((statement) => isProfileStatement(statement))
    )
  );
}
