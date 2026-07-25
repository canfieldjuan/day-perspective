import { expect, test } from "@playwright/test";

function unpublishedBody(date: string, profileType: string) {
  return JSON.stringify({
    status: "profile_not_published",
    date,
    profile_type: profileType,
    detail: "No profile has been published for this date yet."
  });
}

test("out-of-range and malformed addresses get distinct honest states", async ({
  page
}) => {
  await page.goto("/day/1899-12-31");
  await expect(
    page.getByRole("heading", { name: "This date is outside the public range." })
  ).toBeVisible();

  await page.goto("/day/not-a-date");
  await expect(
    page.getByRole("heading", { name: "This address is not a calendar date." })
  ).toBeVisible();
  await expect(page.getByTestId("day-nav")).toBeVisible();
});

test("the skip link is first focus and jumps to main content", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab");
  const skipLink = page.getByRole("link", { name: "Skip to main content" });
  await expect(skipLink).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#main-content$/);
});

test("navigating to an adjacent day moves focus to the new arrival heading", async ({
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
    "data-phase",
    "arrived"
  );
  await page.getByRole("link", { name: "Next day, March 28, 1964" }).click();
  await expect(page.getByTestId("day-arrival")).toContainText("March 28, 1964");
  const focusedText = await page.evaluate(
    () => document.activeElement?.textContent ?? ""
  );
  expect(focusedText).toBe("March 28, 1964");
});
