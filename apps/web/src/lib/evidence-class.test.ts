import { describe, expect, it } from "vitest";

import type { ProfileStatement } from "@day-perspective/contracts";
import { deriveEvidenceClass } from "./evidence-class";

const provenanceBase = {
  published_statement: "Selected.",
  supporting_claims: [],
  dissenting_claims: [],
  source_release: {
    source: "Test Source",
    publisher: null,
    release: "fixture-v1",
    source_url: "https://example.org",
    raw_checksum_sha256: "a".repeat(64),
    retrieved_at: "2026-01-01T00:00:00Z"
  },
  methodology: { name: "Test method", version: "1", description: "Test." }
};

const resolvedClaim = {
  canonical_key: "test:claim",
  version: 1,
  method: "single_source",
  rationale: "Accepted."
};

const derivedValue = {
  kind: "average_daily_births",
  calculation_version: "0.3.0",
  value: {}
};

function statement(overrides: Partial<ProfileStatement> = {}): ProfileStatement {
  return {
    statement_id: "s-1",
    statement: "Synthetic statement used only to test evidence classification.",
    ...overrides
  };
}

function resolvedStatement(details?: Record<string, unknown>): ProfileStatement {
  return statement({
    details,
    provenance: { ...provenanceBase, resolved_claim: resolvedClaim }
  });
}

function derivedStatement(details?: Record<string, unknown>): ProfileStatement {
  return statement({
    details,
    provenance: { ...provenanceBase, derived_value: derivedValue }
  });
}

describe("deriveEvidenceClass", () => {
  it("classifies recorded-section resolved claims as recorded", () => {
    const result = deriveEvidenceClass("recorded_on_this_date", resolvedStatement());
    expect(result.key).toBe("recorded");
    expect(result.label).toBe("Recorded on this date");
    expect(result.caveat).toBeNull();
  });

  it("classifies uniform-allocation derived values as annual daily averages", () => {
    const result = deriveEvidenceClass(
      "typical_day_in_this_year",
      derivedStatement({ temporal_assignment: "uniform_period_allocation" })
    );
    expect(result.key).toBe("daily-average");
    expect(result.label).toBe("Annual daily average");
    expect(result.caveat).toBe("Average across {year} — not a count for this date.");
  });

  it("defaults typical-day derived values without markers to daily averages", () => {
    const result = deriveEvidenceClass("typical_day_in_this_year", derivedStatement());
    expect(result.key).toBe("daily-average");
  });

  it("classifies modeled-allocation derived values as modeled for this date", () => {
    const result = deriveEvidenceClass(
      "typical_day_in_this_year",
      derivedStatement({ temporal_assignment: "modeled_period_allocation" })
    );
    expect(result.key).toBe("date-modeled");
    expect(result.caveat).toBe(
      "Modeled estimate for this date, not a recorded observation."
    );
  });

  it("classifies period_context assignments as period context", () => {
    const result = deriveEvidenceClass(
      "wider_historical_context",
      resolvedStatement({ temporal_assignment: "period_context" })
    );
    expect(result.key).toBe("period-context");
    expect(result.caveat).toBe(
      "Describes the surrounding period, not this date specifically."
    );
  });

  it("classifies editorial_context assignments as period context", () => {
    const result = deriveEvidenceClass(
      "wider_historical_context",
      resolvedStatement({ temporal_assignment: "editorial_context" })
    );
    expect(result.key).toBe("period-context");
  });

  it("defaults wider-context statements without markers to period context", () => {
    const result = deriveEvidenceClass("wider_historical_context", resolvedStatement());
    expect(result.key).toBe("period-context");
  });

  it("classifies curated-claims statements as curated", () => {
    const result = deriveEvidenceClass("curated_claims", resolvedStatement());
    expect(result.key).toBe("curated");
    expect(result.label).toBe("Curated claim");
  });

  it("classifies derived-comparisons statements as app-derived comparisons", () => {
    const result = deriveEvidenceClass("derived_comparisons", derivedStatement());
    expect(result.key).toBe("comparison");
    expect(result.label).toBe("App-derived comparison");
  });

  it("classifies evidence-notes statements as archive notes", () => {
    const result = deriveEvidenceClass("evidence_notes", derivedStatement());
    expect(result.key).toBe("archive-note");
    expect(result.label).toBe("About this evidence");
  });

  it("classifies missing data as unavailable in any section, beating other markers", () => {
    const result = deriveEvidenceClass(
      "wider_historical_context",
      derivedStatement({
        data_status: "missing",
        temporal_assignment: "uniform_period_allocation"
      })
    );
    expect(result.key).toBe("unavailable");
    expect(result.label).toBe("Not available");
  });

  it("classifies provenance-free statements as unclassified in every section", () => {
    for (const section of [
      "recorded_on_this_date",
      "typical_day_in_this_year",
      "wonder_and_progress",
      "evidence_notes"
    ] as const) {
      const result = deriveEvidenceClass(section, statement());
      expect(result.key).toBe("unclassified");
      expect(result.label).toBe("Evidence class unstated");
    }
  });

  it("keeps wonder-and-progress conservative: no section default without markers", () => {
    const result = deriveEvidenceClass("wonder_and_progress", resolvedStatement());
    expect(result.key).toBe("unclassified");
  });

  it("classifies wonder-and-progress by markers when they exist", () => {
    const result = deriveEvidenceClass(
      "wonder_and_progress",
      resolvedStatement({ temporal_assignment: "period_context" })
    );
    expect(result.key).toBe("period-context");
  });

  it("never upgrades a derived value to recorded via the section default", () => {
    const result = deriveEvidenceClass("recorded_on_this_date", derivedStatement());
    expect(result.key).toBe("unclassified");
  });

  it("never labels a resolved claim as a daily average via the section default", () => {
    const result = deriveEvidenceClass("typical_day_in_this_year", resolvedStatement());
    expect(result.key).toBe("unclassified");
  });

  it("keeps recorded when both provenance branches are present", () => {
    const both = statement({
      provenance: {
        ...provenanceBase,
        resolved_claim: resolvedClaim,
        derived_value: derivedValue
      }
    });
    expect(deriveEvidenceClass("recorded_on_this_date", both).key).toBe("recorded");
  });

  it("degrades unknown markers to the section default instead of guessing", () => {
    const result = deriveEvidenceClass(
      "typical_day_in_this_year",
      derivedStatement({ temporal_assignment: "seasonally_adjusted_nonsense" })
    );
    expect(result.key).toBe("daily-average");
  });

  it("never throws on malformed details", () => {
    const malformed: Array<unknown> = [
      null,
      42,
      "details",
      [],
      { temporal_assignment: 9 },
      { data_status: { nested: true } }
    ];
    for (const details of malformed) {
      expect(() =>
        deriveEvidenceClass(
          "typical_day_in_this_year",
          statement({
            details: details as ProfileStatement["details"],
            provenance: { ...provenanceBase, derived_value: derivedValue }
          })
        )
      ).not.toThrow();
    }
  });

  it("uses validated branch presence, not root_type, as the discriminant", () => {
    const lyingRootType = statement({
      provenance: {
        ...provenanceBase,
        root_type: "derived_value",
        resolved_claim: resolvedClaim
      }
    });
    const result = deriveEvidenceClass("recorded_on_this_date", lyingRootType);
    expect(result.key).toBe("recorded");
  });
});

describe("deriveEvidenceClass review-round hardening", () => {
  it("treats a null derived value as unavailable, beating markers and defaults", () => {
    const nullValueDerived = statement({
      details: { temporal_assignment: "uniform_period_allocation" },
      provenance: {
        ...provenanceBase,
        derived_value: { ...derivedValue, value: null }
      }
    });
    expect(
      deriveEvidenceClass("typical_day_in_this_year", nullValueDerived).key
    ).toBe("unavailable");
    expect(
      deriveEvidenceClass("derived_comparisons", nullValueDerived).key
    ).toBe("unavailable");
  });

  it("keeps object-valued derived statements in their normal classes", () => {
    const objectValued = statement({
      details: { temporal_assignment: "uniform_period_allocation" },
      provenance: {
        ...provenanceBase,
        derived_value: { ...derivedValue, value: { average_daily_equivalent: 320470 } }
      }
    });
    expect(
      deriveEvidenceClass("typical_day_in_this_year", objectValued).key
    ).toBe("daily-average");
  });

  it("carries the supplied comparability status in the comparison caveat", () => {
    const withStatus = statement({
      details: { comparability_status: "partially_comparable" },
      provenance: {
        ...provenanceBase,
        derived_value: { ...derivedValue, value: {} }
      }
    });
    const result = deriveEvidenceClass("derived_comparisons", withStatus);
    expect(result.key).toBe("comparison");
    expect(result.caveat).toBe("Comparability: partially_comparable.");
  });

  it("leaves the comparison caveat null when no status is supplied", () => {
    const result = deriveEvidenceClass("derived_comparisons", derivedStatement());
    expect(result.key).toBe("comparison");
    expect(result.caveat).toBeNull();
  });
});
