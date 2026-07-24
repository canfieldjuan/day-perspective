import { expect, test } from "@playwright/test";

test("shows the unpublished profile state and keeps evidence sections distinct", async ({
  page
}) => {
  await page.route("**/api/day/1900-01-01", async (route) => {
    await route.fulfill({
      body: JSON.stringify({
        status: "profile_not_published",
        date: "1900-01-01",
        profile_type: "limited_historical",
        detail: "No profile has been published for this date yet."
      }),
      contentType: "application/json",
      status: 404
    });
  });

  await page.goto("/day/1900-01-01");

  await expect(
    page.getByRole("heading", {
      name: "This day does not have a published profile yet."
    })
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recorded on this date" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Evidence notes" })).toBeVisible();
});
