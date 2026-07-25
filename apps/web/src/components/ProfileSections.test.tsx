import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";

import type { PublishedDayProfile } from "@day-perspective/contracts";
import { ProfileSections } from "./ProfileSections";

describe("ProfileSections provenance", () => {
  it("labels a supporting-record link with the statement source", () => {
    const sections = {
      recorded_on_this_date: [],
      typical_day_in_this_year: [
        {
          statement_id: "average-daily-births",
          statement: "Average daily births in 1964.",
          provenance: {
            root_type: "derived_value",
            published_statement: "Derived from an annual total.",
            derived_value: {
              kind: "average_daily_births",
              calculation_version: "0.3.0",
              value: {}
            },
            supporting_claims: [
              {
                predicate: "annual_births",
                value: { value: "117292.2" },
                source_record_locator: "https://population.un.org/wpp/",
                source_record_hash_sha256: "a".repeat(64)
              },
              {
                predicate: "annual_births",
                value: { value: "117292.2" },
                source_record_locator: "https://population.un.org/wpp/#second",
                source_record_hash_sha256: "c".repeat(64)
              }
            ],
            dissenting_claims: [],
            source_release: {
              source: "UN World Population Prospects",
              publisher: "United Nations",
              release: "WPP 2024",
              source_url: "https://population.un.org/wpp/",
              raw_checksum_sha256: "b".repeat(64),
              retrieved_at: "2026-07-24T00:00:00Z"
            },
            methodology: {
              name: "Annual daily-equivalent method",
              version: "1",
              description: "Annual total divided by days in year."
            }
          }
        }
      ],
      wider_historical_context: [],
      curated_claims: [],
      derived_comparisons: [],
      wonder_and_progress: [],
      evidence_notes: []
    } as PublishedDayProfile["sections"];

    render(<ProfileSections availability="published" sections={sections} />);

    const links = screen.getAllByRole("link", {
        name: "the UN World Population Prospects source record"
      });
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveAttribute("href", "https://population.un.org/wpp/");
    expect(links[1]).toHaveAttribute(
      "href",
      "https://population.un.org/wpp/#second"
    );
  });
});

const emptySections = {
  recorded_on_this_date: [],
  typical_day_in_this_year: [],
  wider_historical_context: [],
  curated_claims: [],
  derived_comparisons: [],
  wonder_and_progress: [],
  evidence_notes: []
} as PublishedDayProfile["sections"];

const testProvenance = {
  published_statement: "Selected.",
  supporting_claims: [],
  dissenting_claims: [],
  source_release: {
    source: "Test Source",
    publisher: null,
    release: "fixture-v1",
    source_url: "https://example.org",
    raw_checksum_sha256: "b".repeat(64),
    retrieved_at: "2026-01-01T00:00:00Z"
  },
  methodology: { name: "Test method", version: "1", description: "Test." }
};

describe("ProfileSections evidence classes", () => {
  it("labels an annual daily average with its chip and year-filled caveat", () => {
    const sections = {
      ...emptySections,
      typical_day_in_this_year: [
        {
          statement_id: "avg-births",
          statement: "Synthetic average statement.",
          details: { temporal_assignment: "uniform_period_allocation" },
          provenance: {
            ...testProvenance,
            derived_value: {
              kind: "average_daily_births",
              calculation_version: "0.3.0",
              value: null
            }
          }
        }
      ]
    } as PublishedDayProfile["sections"];

    render(
      <ProfileSections
        availability="published"
        sections={sections}
        profileDate="1964-03-27"
      />
    );

    const chips = screen.getAllByTestId("evidence-chip");
    expect(chips.map((chip) => chip.textContent)).toEqual(["Annual daily average"]);
    expect(
      screen.getByText("Average across 1964 — not a count for this date.")
    ).toBeInTheDocument();
  });

  it("marks a recorded resolved claim and an unclassified bare statement distinctly", () => {
    const sections = {
      ...emptySections,
      recorded_on_this_date: [
        {
          statement_id: "recorded-1",
          statement: "Synthetic recorded statement.",
          provenance: {
            ...testProvenance,
            resolved_claim: {
              canonical_key: "test:claim",
              version: 1,
              method: "single_source",
              rationale: "Accepted."
            }
          }
        }
      ],
      wonder_and_progress: [
        { statement_id: "bare-1", statement: "Synthetic bare statement." }
      ]
    } as PublishedDayProfile["sections"];

    render(
      <ProfileSections
        availability="published"
        sections={sections}
        profileDate="1964-03-27"
      />
    );

    const chipLabels = screen
      .getAllByTestId("evidence-chip")
      .map((chip) => chip.textContent);
    expect(chipLabels).toContain("Recorded on this date");
    expect(chipLabels).toContain("Evidence class unstated");
  });

  it("shows the disputed badge only when dissenting claims exist", () => {
    const sections = {
      ...emptySections,
      wider_historical_context: [
        {
          statement_id: "disputed-1",
          statement: "Synthetic disputed statement.",
          details: { temporal_assignment: "period_context" },
          provenance: {
            ...testProvenance,
            resolved_claim: {
              canonical_key: "test:disputed",
              version: 1,
              method: "corroborated",
              rationale: "Contested."
            },
            dissenting_claims: [
              {
                predicate: "count",
                value: { value: 24 },
                source_record_locator: "https://example.org/dissent",
                source_record_hash_sha256: "d".repeat(64)
              }
            ]
          }
        },
        {
          statement_id: "undisputed-1",
          statement: "Synthetic undisputed statement.",
          details: { temporal_assignment: "period_context" },
          provenance: {
            ...testProvenance,
            resolved_claim: {
              canonical_key: "test:plain",
              version: 1,
              method: "single_source",
              rationale: "Accepted."
            }
          }
        }
      ]
    } as PublishedDayProfile["sections"];

    render(
      <ProfileSections
        availability="published"
        sections={sections}
        profileDate="1964-03-27"
      />
    );

    const disputedChips = screen
      .getAllByTestId("evidence-chip")
      .filter((chip) => chip.textContent === "Disputed — sources disagree");
    expect(disputedChips).toHaveLength(1);
  });

  it("renders publication quality and source attribution when provided", () => {
    render(
      <ProfileSections
        availability="published"
        sections={emptySections}
        profileDate="1964-03-27"
        quality={{ grade: "B", explanation: "Single validated official source." }}
        sourceAttribution={{
          name: "USGS Earthquake Catalog",
          publisher: "U.S. Geological Survey",
          url: "https://earthquake.usgs.gov"
        }}
      />
    );

    expect(screen.getByText("Publication quality")).toBeInTheDocument();
    expect(screen.getByText("Grade B")).toBeInTheDocument();
    expect(
      screen.getByText("Single validated official source.")
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "USGS Earthquake Catalog" })
    ).toBeInTheDocument();
  });
});
