import { expect, test } from "@playwright/test";

/**
 * The coverage states a reader can actually land in. The archive holds one
 * enriched date in 27,759, so "decades away" and "nothing in that
 * direction" are the ordinary cases — these mocks cover the states the
 * real archive cannot currently produce as well as the ones it can.
 */
function publishedBody(date: string) {
  return JSON.stringify({
    status: "published",
    date,
    profile_type: "standard_statistical",
    manifest_id: "manifest-coverage",
    content_hash: "a".repeat(64),
    profile: {
      schema_version: "1",
      date,
      profile_type: "standard_statistical",
      publication_tier: "context_only",
      sections: {
        typical_day_in_this_year: [
          {
            statement_id: "average-daily-births",
            statement:
              "Average daily births in 1983: about 350,000. This is an average daily equivalent based on the annual total, not an observation for October 12."
          }
        ]
      },
      section_states: { recorded_on_this_date: { status: "available" } }
    }
  });
}

function coverageBody(
  date: string,
  before: string | null,
  after: string | null,
  tier = "context_only"
) {
  return JSON.stringify({
    status: "coverage",
    date,
    profile_type: "standard_statistical",
    publication_tier: tier,
    has_recorded_event: tier !== "context_only",
    sections: { typical_day_in_this_year: 2 },
    nearest_enriched_before: before,
    nearest_enriched_after: after,
    nearest_recorded_event_before: before,
    nearest_recorded_event_after: after
  });
}

async function arrive(
  page: import("@playwright/test").Page,
  date: string,
  coverage: string | null
) {
  await page.route(`**/api/day/${date}`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      status: 200,
      body: publishedBody(date)
    });
  });
  await page.route(`**/api/coverage/${date}`, async (route) => {
    if (coverage === null) {
      await route.fulfill({ status: 503, body: "{}" });
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      status: 200,
      body: coverage
    });
  });
  await page.goto(`/day/${date}`);
}

test("a context-only date says what it has and what it does not", async ({
  page
}) => {
  await arrive(page, "1983-10-12", coverageBody("1983-10-12", "1964-03-27", null));

  await expect(page.getByTestId("publication-tier")).toContainText(
    "This date currently has demographic context only."
  );
  await expect(page.getByTestId("publication-tier")).toContainText(
    "No recorded events are published for October 12, 1983."
  );
});

test("a distant enriched date is named exactly, never called nearby", async ({
  page
}) => {
  await arrive(page, "1983-10-12", coverageBody("1983-10-12", "1964-03-27", null));

  const discovery = page.getByTestId("coverage-discovery");
  await expect(discovery).toContainText("The closest evidence-backed date is farther away");
  await expect(discovery).toContainText("March 27, 1964, nineteen years earlier.");
  await expect(
    page.getByRole("link", { name: "Jump to March 27, 1964" })
  ).toBeVisible();

  const text = ((await discovery.textContent()) ?? "").toLowerCase();
  for (const word of ["soon", "nearby", "just ahead"]) {
    expect(text).not.toContain(word);
  }
});

test("the empty direction is explained rather than left as a silent gap", async ({
  page
}) => {
  await arrive(page, "1983-10-12", coverageBody("1983-10-12", "1964-03-27", null));

  await expect(page.getByTestId("coverage-discovery")).toContainText(
    "No evidence-backed date is currently published after October 12, 1983."
  );
});

test("both directions are offered without preselecting either", async ({
  page
}) => {
  await arrive(
    page,
    "1983-10-12",
    coverageBody("1983-10-12", "1975-09-03", "1984-03-27")
  );

  const discovery = page.getByTestId("coverage-discovery");
  await expect(discovery).toContainText("Closest evidence-backed dates");
  await expect(discovery).toContainText("September 3, 1975 · eight years earlier");
  await expect(discovery).toContainText("March 27, 1984 · about five months later");
  await expect(page.getByRole("link", { name: "Go earlier" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Go later" })).toBeVisible();
});

test("an archive with no enriched dates offers no journey to nowhere", async ({
  page
}) => {
  await arrive(page, "1983-10-12", coverageBody("1983-10-12", null, null));

  await expect(page.getByTestId("coverage-discovery")).toContainText(
    "No evidence-backed dates are currently available from this archive index."
  );
});

test("unreadable coverage claims nothing about the archive", async ({ page }) => {
  await arrive(page, "1983-10-12", null);

  await expect(page.getByTestId("day-arrival")).toBeVisible();
  await expect(page.getByTestId("coverage-discovery")).toHaveCount(0);
  await expect(page.getByTestId("publication-tier")).toHaveCount(0);
  // Silence, not a claim that the archive is empty.
  await expect(page.getByText(/No evidence-backed dates/)).toHaveCount(0);
});
