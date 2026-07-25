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
