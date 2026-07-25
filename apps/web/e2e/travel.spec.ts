import { expect, test } from "@playwright/test";

function unpublishedBody(date: string, profileType: string) {
  return JSON.stringify({
    status: "profile_not_published",
    date,
    profile_type: profileType,
    detail: "No profile has been published for this date yet."
  });
}

test("arrival choreography follows the motion preference", async ({ page }) => {
  await page.route("**/api/day/1964-03-27", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      status: 404,
      body: unpublishedBody("1964-03-27", "standard_statistical")
    });
  });

  await page.goto("/day/1964-03-27");
  const shell = page.getByTestId("travel-shell");
  await expect(shell).toHaveAttribute("data-phase", "arrived");
  await expect(shell).toHaveAttribute("data-entry", "initial");

  const reduced = await page.evaluate(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
  const animationName = await page
    .getByTestId("stratum-recorded_on_this_date")
    .evaluate((element) => getComputedStyle(element).animationName);
  if (reduced) {
    expect(animationName).toBe("none");
  } else {
    expect(animationName).toBe("stratum-arrive");
  }
});

test("adjacent-date arrivals take the quick path, not the full reveal", async ({
  page
}) => {
  for (const date of ["1964-03-27", "1964-03-28"]) {
    await page.route(`**/api/day/${date}`, async (route) => {
      await route.fulfill({
        contentType: "application/json",
        status: 404,
        body: unpublishedBody(date, "standard_statistical")
      });
    });
  }

  await page.goto("/day/1964-03-27");
  await expect(page.getByTestId("travel-shell")).toHaveAttribute(
    "data-entry",
    "initial"
  );

  await page.getByRole("link", { name: "Next day, March 28, 1964" }).click();
  await expect(page.getByTestId("day-arrival")).toContainText("March 28, 1964");
  await expect(page.getByTestId("travel-shell")).toHaveAttribute(
    "data-phase",
    "arrived"
  );
  await expect(page.getByTestId("travel-shell")).toHaveAttribute(
    "data-entry",
    "adjacent"
  );
});
