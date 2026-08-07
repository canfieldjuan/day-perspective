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

/**
 * Which canonical event a recorded statement describes.
 *
 * Grouping is stated rather than inferred from array position. A renderer that
 * guessed group boundaries from order would break the moment a date holds three
 * events, or an event contributes a single statement.
 *
 * `event_group_key` is stable across republication and opaque: it is derived
 * from the event's identity resolution, not from a database row id, so it means
 * the same thing to a browser without publishing an internal identifier that
 * invites being treated as an address.
 *
 * On a **fully grouped** recorded section, exactly one group carries
 * `featured: true`, and that group — and only that group — is `event_order` 0.
 * `predicate_order` runs from 0 within each group, so a group stays correctly
 * ordered when rendered on its own. `event_order` is unique across groups but
 * need not be contiguous.
 *
 * A **partially grouped** section may declare no featured group at all, and
 * that is a valid response rather than a malformed one. The publisher omits
 * this metadata for a statement whose owning event cannot be resolved, and
 * nothing makes the featured event immune: an ambiguous source release can own
 * it, leaving only secondary groups declared. Such a section renders flat
 * (UI_UX_CONTRACT C-3.5.5), so consumers must apply the cross-group invariants
 * above only when every statement in the section carries a group.
 */
export interface ProfileStatementEventGroup {
  event_group_key: string;
  event_title: string;
  featured: boolean;
  event_order: number;
  predicate_order: number;
}

export interface ProfileStatement {
  statement_id: string;
  statement: string;
  /** Present on recorded-event statements published from G3b-2b onward. */
  event_group?: ProfileStatementEventGroup;
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
 * How much a published profile actually offers, ordered sparse to rich —
 * and only that. A date carrying only annual demographic context is useful,
 * but it is not equivalent to a date with a recorded event.
 *
 * This axis says nothing about who checked the content (REVIEW_STATUSES) or
 * how strong its weakest evidence is (QUALITY_FLOORS). The retired
 * `reviewed_enriched` fused richness with review, so the archive could not
 * describe a reviewed context page or an unreviewed enriched one.
 *
 * A tier rises only for content tied to the specific date. Annual averages,
 * annual conflict counts and period comparisons are all `context_only`
 * however many a page carries.
 */
export const PUBLICATION_TIERS = [
  "context_only",
  "partially_enriched",
  "enriched"
] as const;

export type PublicationTier = (typeof PUBLICATION_TIERS)[number];

/** Who or what validated a profile's published content. */
export const REVIEW_STATUSES = [
  "automated_only",
  "review_pending",
  "human_reviewed"
] as const;

export type ReviewStatus = (typeof REVIEW_STATUSES)[number];

/**
 * The weakest graded evidence among a profile's published content.
 * `not_assessed` is the honest answer when any item's grade cannot be
 * ranked: the floor is then at best the weakest letter seen, possibly worse.
 */
export const QUALITY_FLOORS = ["A", "B", "C", "D", "not_assessed"] as const;

export type QualityFloor = (typeof QUALITY_FLOORS)[number];

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
  /**
   * @deprecated Superseded by `source_attributions`. A singular attribution
   * names one source, which is false on a date whose recorded section rests on
   * several — and false in the direction that flatters whichever publisher
   * wrote last. Retained so profiles published before G3b-2b still typecheck.
   */
  source_attribution?: { name: string; publisher: string; url: string };
  /**
   * Every source whose evidence supports this profile, one entry each.
   * Statement-level provenance remains authoritative; this is the page-level
   * summary of it.
   *
   * `publisher` and `url` are absent, not `""`, when the source's publisher
   * or canonical URL is not recorded. Both are nullable upstream, and a
   * present-but-empty string would make "unknown" and "the empty string"
   * the same payload -- unavailable data encoded as though it were present.
   * `name` stays required: it is non-nullable upstream, so an empty name
   * means the payload is corrupt, not that the source is unattributed.
   */
  source_attributions?: Array<{ name: string; publisher?: string; url?: string }>;
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
  /**
   * Independent of the tier, and rendered independently. "Enriched" answers
   * how much is here; "reviewed" answers who or what checked it; the floor
   * answers how strong the weakest included evidence is.
   */
  review_status: ReviewStatus;
  quality_floor: QualityFloor;
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
 * Random enriched-date discovery. Answered only when the archive holds a
 * date richer than annual context; the absence case is explicit so the
 * interface can hide the control rather than offer a journey to nowhere.
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
