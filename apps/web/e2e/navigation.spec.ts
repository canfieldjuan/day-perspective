import { expect, test } from "@playwright/test";

// Same undiagnosed stall tracked on epic #7 and mitigated in states.spec.ts:
// a click-initiated soft navigation intermittently hangs under parallel e2e
// load, never in isolation (6/6 clean). #44 established that hydration is
// not the cause, so this is mitigation, not a fix. Coverage requests on
// every day page raise the load and made it reachable here too.
test.describe.configure({ retries: 2 });

function unpublishedBody(date: string, profileType: string) {
  return JSON.stringify({
    status: "profile_not_published",
    date,
    profile_type: profileType,
    detail: "No profile has been published for this date yet."
  });
}

test("previous and next step blindly onto honest unpublished arrivals", async ({
  page
}) => {
  for (const [date, type] of [
    ["1964-03-27", "standard_statistical"],
    ["1964-03-28", "standard_statistical"],
    ["1964-03-26", "standard_statistical"]
  ] as const) {
    await page.route(`**/api/day/${date}`, async (route) => {
      await route.fulfill({
        contentType: "application/json",
        status: 404,
        body: unpublishedBody(date, type)
      });
    });
  }

  await page.goto("/day/1964-03-27");
  await expect(page).toHaveTitle("March 27, 1964 — Day Perspective");
  // states.spec.ts already gates its click on this; these specs did not.
  // data-phase flips to "arrived" from client state, so it is a real
  // hydration signal rather than a sleep. This makes the specs consistent
  // — it is NOT a diagnosis of the focus flake, which stalls even with
  // this wait in place.
  await expect(page.getByTestId("travel-shell")).toHaveAttribute(
    "data-phase",
    "arrived"
  );

  await page.getByRole("link", { name: "Next day, March 28, 1964" }).click();
  await expect(page).toHaveURL(/\/day\/1964-03-28$/);
  await expect(page.getByTestId("day-arrival")).toContainText("March 28, 1964");
  await expect(
    page.getByRole("heading", {
      name: "This day does not have a published profile yet."
    })
  ).toBeVisible();

  await expect(page.getByTestId("travel-shell")).toHaveAttribute(
    "data-phase",
    "arrived"
  );
  await page.getByRole("link", { name: "Previous day, March 27, 1964" }).click();
  await expect(page).toHaveURL(/\/day\/1964-03-27$/);
});

test("non-canonical date paths redirect to the canonical form", async ({ page }) => {
  await page.route("**/api/day/1964-03-27", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      status: 404,
      body: unpublishedBody("1964-03-27", "standard_statistical")
    });
  });

  await page.goto("/day/1964-3-27");
  await expect(page).toHaveURL(/\/day\/1964-03-27$/);
  await expect(page.getByTestId("day-arrival")).toContainText("March 27, 1964");
});

test("the shell edge omits the impossible direction instead of wrapping", async ({
  page
}) => {
  await page.route("**/api/day/1900-01-01", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      status: 404,
      body: unpublishedBody("1900-01-01", "limited_historical")
    });
  });

  await page.goto("/day/1900-01-01");
  await expect(page.getByTestId("day-nav")).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Next day, January 2, 1900" })
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: /Previous day/ })
  ).toHaveCount(0);
});

test("a malformed path keeps recovery navigation without inventing dates", async ({
  page
}) => {
  await page.goto("/day/not-a-date");
  await expect(page.getByTestId("day-nav")).toBeVisible();
  await expect(page.getByRole("link", { name: /Previous day/ })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /Next day/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Random day" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Another date" })).toBeVisible();
});

test("random day lands inside the shell with navigation still present", async ({
  page
}) => {
  await page.route("**/api/day/**", async (route) => {
    const date = decodeURIComponent(route.request().url().split("/").pop() ?? "");
    await route.fulfill({
      contentType: "application/json",
      status: 404,
      body: unpublishedBody(date, "standard_statistical")
    });
  });

  await page.goto("/day/1964-03-27");
  await expect(page.getByTestId("travel-shell")).toHaveAttribute(
    "data-phase",
    "arrived"
  );
  await page.getByRole("button", { name: "Random day" }).click();
  await expect(page).toHaveURL(/\/day\/\d{4}-\d{2}-\d{2}$/);
  await expect(page.getByTestId("day-nav")).toBeVisible();
});

test("the canonical redirect is a real HTTP 308 before streaming", async ({
  page
}) => {
  const response = await page.request.get("/day/1964-3-27", {
    maxRedirects: 0
  });
  expect(response.status()).toBe(308);
  expect(response.headers()["location"]).toContain("/day/1964-03-27");
});
