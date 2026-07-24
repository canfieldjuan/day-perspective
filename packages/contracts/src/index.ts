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
    published_statement: string;
    resolved_claim: {
      canonical_key: string;
      version: number;
      method: string;
      rationale: string;
    };
    supporting_claims: Array<{
      predicate: string;
      value: Record<string, unknown> | null;
      source_record_locator: string;
      source_record_hash_sha256: string;
    }>;
    dissenting_claims: Array<{
      predicate?: string;
      value?: Record<string, unknown> | null;
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
