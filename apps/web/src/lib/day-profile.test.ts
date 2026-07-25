import { describe, expect, it } from "vitest";
import { isPublishedProfileResponse } from "./day-profile";

describe("isPublishedProfileResponse", () => {
  it("accepts contract-valid dissent without source-record fields", () => {
    expect(
      isPublishedProfileResponse(
        {
          status: "published",
          date: "1964-03-27",
          profile_type: "standard_statistical",
          manifest_id: "manifest-dissent",
          content_hash: "a".repeat(64),
          profile: {
            schema_version: "1",
            date: "1964-03-27",
            profile_type: "standard_statistical",
            sections: {
              recorded_on_this_date: [
                {
                  statement_id: "dissent-test",
                  statement: "A test statement with visible dissent.",
                  provenance: {
                    published_statement: "Selected.",
                    resolved_claim: {
                      canonical_key: "test:dissent",
                      version: 1,
                      method: "editorial_review",
                      rationale: "Testing the contract boundary."
                    },
                    supporting_claims: [
                      {
                        predicate: "test",
                        value: null,
                        source_record_locator: "record:test",
                        source_record_hash_sha256: "b".repeat(64)
                      }
                    ],
                    dissenting_claims: [{ predicate: "test", value: null }],
                    source_release: {
                      source: "Test source",
                      publisher: null,
                      release: "test-v1",
                      source_url: "https://example.invalid/source",
                      raw_checksum_sha256: "c".repeat(64),
                      retrieved_at: "2026-07-24T00:00:00Z"
                    },
                    methodology: {
                      name: "Test methodology",
                      version: "1",
                      description: "Test-only."
                    }
                  }
                }
              ]
            }
          }
        },
        "1964-03-27"
      )
    ).toBe(true);
  });

  it("rejects malformed section-state reasons before rendering", () => {
    expect(
      isPublishedProfileResponse(
        {
          status: "published",
          date: "1964-03-27",
          profile_type: "standard_statistical",
          manifest_id: "manifest-section-state",
          content_hash: "d".repeat(64),
          profile: {
            schema_version: "1",
            date: "1964-03-27",
            profile_type: "standard_statistical",
            sections: {},
            section_states: {
              recorded_on_this_date: {
                status: "not_yet_supported",
                reason: { unsafe: "React child" }
              }
            }
          }
        },
        "1964-03-27"
      )
    ).toBe(false);
  });

  it("rejects malformed optional source attribution before rendering", () => {
    expect(
      isPublishedProfileResponse(
        {
          status: "published",
          date: "1964-03-27",
          profile_type: "standard_statistical",
          manifest_id: "manifest-source-attribution",
          content_hash: "e".repeat(64),
          profile: {
            schema_version: "1",
            date: "1964-03-27",
            profile_type: "standard_statistical",
            sections: {},
            source_attribution: {
              name: "USGS",
              publisher: "U.S. Geological Survey"
            }
          }
        },
        "1964-03-27"
      )
    ).toBe(false);
  });
});
