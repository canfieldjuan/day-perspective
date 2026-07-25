import { expect, test } from "@playwright/test";

test("renders the golden earthquake and public provenance chain", async ({ page }) => {
  await page.route("**/api/day/1964-03-27", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      status: 200,
      body: JSON.stringify({
        status: "published",
        date: "1964-03-27",
        profile_type: "standard_statistical",
        manifest_id: "golden-manifest",
        content_hash: "a".repeat(64),
        profile: {
          schema_version: "1",
          date: "1964-03-27",
          profile_type: "standard_statistical",
          sections: {
            recorded_on_this_date: [
              {
                statement_id: "event-magnitude",
                statement: "USGS reports a magnitude of 9.2 Mw.",
                provenance: {
                  published_statement: "Selected.",
                  resolved_claim: {
                    canonical_key: "usgs:event:magnitude",
                    version: 1,
                    method: "single_source",
                    rationale: "Accepted official USGS record."
                  },
                  supporting_claims: [
                    {
                      predicate: "magnitude",
                      value: { value: 9.2 },
                      source_record_locator: "https://earthquake.usgs.gov/example",
                      source_record_hash_sha256: "b".repeat(64)
                    }
                  ],
                  dissenting_claims: [],
                  source_release: {
                    source: "USGS Earthquake Catalog",
                    publisher: "U.S. Geological Survey",
                    release: "fixture-v1",
                    source_url: "https://earthquake.usgs.gov",
                    raw_checksum_sha256: "c".repeat(64),
                    retrieved_at: "2026-07-24T00:00:00Z"
                  },
                  methodology: {
                    name: "USGS authoritative resolution",
                    version: "1",
                    description: "Deterministic."
                  }
                }
              }
            ],
            typical_day_in_this_year: [],
            wider_historical_context: [],
            curated_claims: [],
            derived_comparisons: [],
            wonder_and_progress: [],
            evidence_notes: []
          }
        }
      })
    });
  });

  await page.goto("/day/1964-03-27");
  await expect(page.getByText("USGS reports a magnitude of 9.2 Mw.")).toBeVisible();
  await expect(page.getByTestId("evidence-chip")).toHaveText("Recorded on this date");
  await page.getByText("Why can the app say this?").click();
  await expect(
    page.getByRole("link", {
      name: "the USGS Earthquake Catalog source record"
    })
  ).toBeVisible();
  await expect(page.getByText("None in this publication.")).toBeVisible();
});
