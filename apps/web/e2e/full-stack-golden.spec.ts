import { expect, test } from "@playwright/test";
import type { CoverageDateResponse } from "@day-perspective/contracts";

test("serves the reviewed golden artifact through the complete runtime path", async ({
  page
}) => {
  test.skip(
    process.env.DAY_PERSPECTIVE_FULL_STACK !== "1",
    "Run through make web-e2e-full-stack with FastAPI and a published fixture."
  );

  await page.goto("/day/1964-03-27");
  await expect(page.getByText("USGS reports a magnitude of 9.2 MW.")).toBeVisible();
  await expect(
    page.getByText(
      "Historical America/Anchorage civil-time rules assign the occurrence to March 27, 1964 locally."
    )
  ).toBeVisible();
  await expect(page.getByText("Evidence quality: B")).toBeVisible();
  await expect(
    page.getByText("Average daily births in 1964: about 320,470.")
  ).toBeVisible();
  await expect(
    page.getByText(
      /Average daily births in 1964.*average daily equivalent based on the annual total, not an observation for March 27/
    )
  ).toBeVisible();
  await expect(
    page.getByText(
      /25 state-based armed conflicts as active at some point in 1964/
    )
  ).toBeVisible();

  const qualityCard = page.getByTestId("stratum-evidence_notes");
  await qualityCard
    .getByRole("button", { name: "Why can the app say this?" })
    .first()
    .click();
  const evidencePanel = page.getByTestId("evidence-panel");
  await expect(evidencePanel.getByText(/Derived value/)).toBeVisible();
  await expect(evidencePanel.getByText(/calculation version 0.3.0/)).toBeVisible();
  await evidencePanel.getByRole("button", { name: "Close evidence panel" }).click();
  await expect(page.getByTestId("publication-integrity")).toContainText("Manifest");
  await expect(page.getByTestId("publication-integrity")).toContainText(
    "Content hash"
  );
});

test("an ordinary context-only date is honest about what it holds", async ({
  page
}) => {
  test.skip(
    process.env.DAY_PERSPECTIVE_FULL_STACK !== "1",
    "Run through make web-e2e-full-stack with FastAPI and a published archive."
  );

  // The definition of done for AA6, against the real archive rather than a
  // mock: an ordinary 1983 date must say what it has, what it lacks, and
  // where the nearest reviewed evidence actually is.
  await page.goto("/day/1983-10-12");

  await expect(page.getByTestId("publication-tier")).toContainText(
    "This date currently has demographic and period context only."
  );
  await expect(page.getByTestId("publication-tier")).toContainText(
    "No recorded events are published for October 12, 1983."
  );

  const discovery = page.getByTestId("coverage-discovery");
  await expect(discovery).toContainText("Find enriched dates");
  // Live enrichment may add closer dates. Verify the real coverage contract
  // reaches the browser instead of freezing the archive at its first event.
  const response = await page.request.get("/api/coverage/1983-10-12");
  expect(response.ok()).toBe(true);
  const coverage: CoverageDateResponse = await response.json();
  expect(coverage.status).toBe("coverage");
  expect(coverage.date).toBe("1983-10-12");
  // The reviewed USGS fixture guarantees at least an earlier destination.
  expect(typeof coverage.nearest_enriched_before).toBe("string");
  const neighbours = [
    { date: coverage.nearest_enriched_before, direction: "before" },
    { date: coverage.nearest_enriched_after, direction: "after" }
  ];
  await expect(discovery.getByRole("link")).toHaveCount(
    neighbours.filter(({ date }) => date !== null).length
  );
  for (const neighbour of neighbours) {
    if (neighbour.date === null) {
      await expect(discovery).toContainText(
        `No enriched date is currently published ${neighbour.direction} October 12, 1983.`
      );
    } else {
      const label = new Intl.DateTimeFormat("en-US", {
        year: "numeric", month: "long", day: "numeric", timeZone: "UTC"
      }).format(new Date(`${neighbour.date}T00:00:00Z`));
      await expect(discovery).toContainText(label);
      await expect(
        discovery.locator(`a[href="/day/${neighbour.date}"]`)
      ).toBeVisible();
    }
  }

  // Chronological navigation is untouched by this slice.
  await expect(
    page.getByRole("navigation", { name: "Date navigation" })
  ).toBeVisible();
});
