import { expect, test } from "@playwright/test";

test("a valid date departs to its day page", async ({ page }) => {
  await page.route("**/api/day/1964-03-27", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      status: 404,
      body: JSON.stringify({
        status: "profile_not_published",
        date: "1964-03-27",
        profile_type: "standard_statistical",
        detail: "No profile has been published for this date yet."
      })
    });
  });

  await page.goto("/");
  await expect(page.getByTestId("era-horizon")).toBeVisible();

  await page.getByLabel("Date").fill("1964-03-27");
  await expect(
    page.getByText("Standard statistical era · 1950–1988").first()
  ).toBeVisible();

  await page.getByRole("button", { name: "Open profile" }).click();
  await expect(page).toHaveURL(/\/day\/1964-03-27$/);
  await expect(page.getByTestId("day-arrival")).toContainText("March 27, 1964");
  await expect(page.getByTestId("travel-shell")).toHaveAttribute(
    "data-entry",
    "initial"
  );
});

test("an unsupported date shows a recoverable alert without navigating", async ({
  page
}) => {
  await page.goto("/");
  await page.getByLabel("Date").fill("1800-01-01");
  await page.getByRole("button", { name: "Open profile" }).click();
  const formAlert = page
    .getByRole("alert")
    .filter({ hasText: "Enter a valid date" });
  await expect(formAlert).toContainText(
    "Enter a valid date from 1900-01-01 through 2025-12-31"
  );
  await expect(page).toHaveURL(/\/$/);

  await page.getByLabel("Date").fill("1964-03-27");
  await expect(formAlert).toHaveCount(1);
});

test("the keyboard-only path departs with Enter", async ({ page }) => {
  await page.route("**/api/day/2000-01-01", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      status: 404,
      body: JSON.stringify({
        status: "profile_not_published",
        date: "2000-01-01",
        profile_type: "enhanced_structured",
        detail: "No profile has been published for this date yet."
      })
    });
  });

  await page.goto("/");
  await page.getByLabel("Date").focus();
  await page.getByLabel("Date").fill("2000-01-01");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/day\/2000-01-01$/);
});
