export const PUBLIC_DATE_MIN = "1900-01-01";
export const PUBLIC_DATE_MAX = "2025-12-31";

export type ProfileType =
  | "limited_historical"
  | "standard_statistical"
  | "enhanced_structured";

export const DAY_PROFILE_SECTION_KEYS = [
  "recorded_on_this_date",
  "typical_day_in_this_year",
  "wider_historical_context",
  "curated_claims",
  "derived_comparisons",
  "wonder_and_progress",
  "evidence_notes"
] as const;

export type DayProfileSectionKey = (typeof DAY_PROFILE_SECTION_KEYS)[number];

export interface ProfileStatement {
  statement_id: string;
  statement: string;
  provenance_note?: string;
  details?: Record<string, unknown>;
  provenance?: {
    root_type?: "resolved_claim" | "derived_value";
    published_statement: string;
    resolved_claim?: {
      canonical_key: string;
      version: number;
      method: string;
      rationale: string;
    };
    derived_value?: {
      kind: string;
      calculation_version: string;
      value: Record<string, unknown> | null;
    };
    supporting_claims: Array<{
      predicate: string;
      value: Record<string, unknown> | null;
      source_record_locator: string;
      source_record_hash_sha256: string;
    }>;
    dissenting_claims: Array<{
      predicate: string;
      value: Record<string, unknown> | null;
      source_record_locator: string;
      source_record_hash_sha256: string;
    }>;
    source_release: {
      source: string;
      publisher: string | null;
      release: string;
      source_url: string;
      raw_checksum_sha256: string;
      retrieved_at: string;
    };
    methodology: {
      name: string;
      version: string;
      description: string;
    };
  };
}

/**
 * How much a published profile actually offers, ordered sparse to rich.
 * A date carrying only annual demographic context is useful, but it is not
 * equivalent to a date with a reviewed recorded event.
 */
export const PUBLICATION_TIERS = [
  "context_only",
  "partially_enriched",
  "reviewed_enriched"
] as const;

export type PublicationTier = (typeof PUBLICATION_TIERS)[number];

export interface PublishedDayProfile {
  schema_version: "1";
  date: string;
  profile_type: ProfileType;
  sections: Partial<Record<DayProfileSectionKey, ProfileStatement[]>>;
  section_states?: Partial<
    Record<
      DayProfileSectionKey,
      { status: "available" | "not_yet_supported"; reason?: string }
    >
  >;
  publication_tier?: PublicationTier;
  quality?: { grade: string; explanation: string };
  source_attribution?: { name: string; publisher: string; url: string };
}

export interface ProfileNotPublished {
  status: "profile_not_published";
  date: string;
  profile_type: ProfileType;
  detail: string;
}

export interface PublishedProfileResponse {
  status: "published";
  date: string;
  profile_type: ProfileType;
  manifest_id: string;
  content_hash: string;
  profile: PublishedDayProfile;
}

/**
 * Coverage index (epic #32). Once every supported date carries annual
 * context, "is anything published?" stops distinguishing anything. These
 * shapes carry how rich a date is and where the nearest richer date lies,
 * so navigation can be honest about a dense archive.
 */
export type CoverageSectionCounts = Partial<Record<DayProfileSectionKey, number>>;

export interface CoverageDateResponse {
  status: "coverage";
  date: string;
  profile_type: ProfileType;
  publication_tier: PublicationTier;
  has_recorded_event: boolean;
  /** Published statement counts per section, from immutable evidence rows. */
  sections: CoverageSectionCounts;
  /** Nearest date offering more than annual context, or null if none. */
  nearest_enriched_before: string | null;
  nearest_enriched_after: string | null;
  nearest_recorded_event_before: string | null;
  nearest_recorded_event_after: string | null;
}

/** A date with no published profile is absent from the index, never empty. */
export interface CoverageNotIndexedResponse {
  status: "coverage_not_indexed";
  date: string;
  detail: string;
}

export interface CoverageSummaryResponse {
  status: "coverage_summary";
  total_published: number;
  by_tier: Record<PublicationTier, number>;
  with_recorded_event: number;
  earliest: string | null;
  latest: string | null;
  supported_range: { minimum: string; maximum: string };
}

/**
 * Random enriched-date discovery. Answered only when the archive actually
 * holds a date richer than annual context; the absence case is explicit so
 * the interface can hide the control rather than offer a journey to
 * nowhere.
 */
export interface RandomEnrichedResponse {
  status: "enriched_date";
  date: string;
}

export interface NoEnrichedDatesResponse {
  status: "no_enriched_dates";
  detail: string;
}

export function profileTypeForDate(value: string): ProfileType | undefined {
  if (value < PUBLIC_DATE_MIN || value > PUBLIC_DATE_MAX) {
    return undefined;
  }
  if (value <= "1949-12-31") {
    return "limited_historical";
  }
  if (value <= "1988-12-31") {
    return "standard_statistical";
  }
  return "enhanced_structured";
}
