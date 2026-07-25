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
