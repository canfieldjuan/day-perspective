import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";

import type { ProfileStatement } from "@day-perspective/contracts";
import { EvidencePanel } from "./EvidencePanel";

const statement: ProfileStatement = {
  statement_id: "s-evidence",
  statement: "Synthetic statement for the evidence panel.",
  provenance: {
    published_statement: "Selected for publication by test.",
    resolved_claim: {
      canonical_key: "test:claim",
      version: 2,
      method: "corroborated",
      rationale: "Two sources agree."
    },
    supporting_claims: [
      {
        predicate: "magnitude",
        value: { value: 9.2 },
        source_record_locator: "https://example.org/support",
        source_record_hash_sha256: "b".repeat(64)
      }
    ],
    dissenting_claims: [
      {
        predicate: "magnitude",
        value: { value: 9.1 },
        source_record_locator: "https://example.org/dissent",
        source_record_hash_sha256: "d".repeat(64)
      }
    ],
    source_release: {
      source: "Test Catalog",
      publisher: "Test Publisher",
      release: "fixture-v1",
      source_url: "https://example.org",
      raw_checksum_sha256: "c".repeat(64),
      retrieved_at: "2026-01-01T00:00:00Z"
    },
    methodology: {
      name: "Test method",
      version: "3",
      description: "Deterministic test method."
    }
  }
};

describe("EvidencePanel", () => {
  it("presents dissenting records with the same completeness as supporting ones", () => {
    render(
      <EvidencePanel
        open
        statement={statement}
        onClose={() => undefined}
      />
    );

    expect(
      screen.getByRole("link", { name: "the Test Catalog source record" })
    ).toHaveAttribute("href", "https://example.org/support");
    expect(
      screen.getByRole("link", { name: "the dissenting Test Catalog source record" })
    ).toHaveAttribute("href", "https://example.org/dissent");
    expect(screen.getByText(/corroborated/)).toBeInTheDocument();
    expect(screen.getByText(/Test method/)).toBeInTheDocument();
  });

  it("renders the statement's quality grade inside the chain when provided", () => {
    render(
      <EvidencePanel
        open
        qualityGrade="B"
        statement={statement}
        onClose={() => undefined}
      />
    );
    expect(screen.getByText("Grade B")).toBeInTheDocument();
  });

  it("states the canonical empty-dissent line when no dissent exists", () => {
    const undisputed: ProfileStatement = {
      ...statement,
      provenance: { ...statement.provenance!, dissenting_claims: [] }
    };
    render(
      <EvidencePanel open statement={undisputed} onClose={() => undefined} />
    );
    expect(screen.getByText("None in this publication.")).toBeInTheDocument();
  });

  it("closes from its explicit close action", () => {
    let closed = false;
    render(
      <EvidencePanel
        open
        statement={statement}
        onClose={() => {
          closed = true;
        }}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "Close evidence panel" }));
    expect(closed).toBe(true);
  });
});

describe("an app-derived comparison names its model card", () => {
  it("shows the card that governs the computation", () => {
    // #56: no comparison ships without a card the reader can reach. Read
    // from the derived value already on screen, so the card named is the
    // one governing that computation.
    render(
      <EvidencePanel
        open
        statement={{
          statement_id: "conflict-vs-median-1964",
          statement: "Day Perspective compares this: …",
          provenance: {
            root_type: "derived_value",
            published_statement: "25 active conflicts against a median of 36.",
            derived_value: {
              kind: "conflict_count_vs_reference_median",
              calculation_version: "1.0.0",
              value: { model_card: "conflict-count-vs-reference-median-v1" }
            },
            supporting_claims: [],
            dissenting_claims: [],
            source_release: {
              source: "Day Perspective (derived)",
              publisher: "Day Perspective",
              release: "conflict-count-vs-reference-median-v1",
              source_url: "docs/MODEL_CARDS/conflict-count-vs-reference-median-v1.md",
              raw_checksum_sha256: "abc",
              retrieved_at: "2026-07-27T00:00:00+00:00"
            },
            methodology: { name: "m", version: "1", description: "d" }
          }
        }}
        onClose={() => {}}
      />
    );

    const link = screen.getByRole("link", {
      name: "conflict-count-vs-reference-median-v1"
    });
    expect(link).toHaveAttribute(
      "href",
      "https://github.com/canfieldjuan/day-perspective/blob/main/docs/MODEL_CARDS/conflict-count-vs-reference-median-v1.md"
    );
  });

  it("shows no model card row for a derivation that has none", () => {
    // Every other derived value on the site is a source-backed computation
    // with no card, and inventing an empty row would imply one exists.
    render(
      <EvidencePanel
        open
        statement={{
          statement_id: "average-daily-births",
          statement: "Average daily births in 1964: about 320,470.",
          provenance: {
            root_type: "derived_value",
            published_statement: "Annual total divided by 366 days.",
            derived_value: {
              kind: "daily_equivalent",
              calculation_version: "0.3.0",
              value: { per_day: 320470 }
            },
            supporting_claims: [],
            dissenting_claims: [],
            source_release: {
              source: "UN WPP",
              publisher: "UN",
              release: "2024",
              source_url: "https://example.invalid",
              raw_checksum_sha256: "abc",
              retrieved_at: "2026-07-27T00:00:00+00:00"
            },
            methodology: { name: "m", version: "1", description: "d" }
          }
        }}
        onClose={() => {}}
      />
    );

    expect(screen.queryByText("Model card")).not.toBeInTheDocument();
  });
});
