import { expect, test } from "@playwright/test";

test("loading pulse animates only when motion is allowed", async ({ page }) => {
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });

  await page.route("**/api/day/1964-03-27", async (route) => {
    await gate;
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

  await page.goto("/day/1964-03-27");

  const loadingLine = page.locator(".loading-line").first();
  await expect(loadingLine).toBeVisible();

  const reducedMotion = await page.evaluate(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
  const animationName = await loadingLine.evaluate(
    (element) => getComputedStyle(element).animationName
  );

  expect(animationName).toBe(reducedMotion ? "none" : "pulse");

  release();
  await expect(
    page.getByRole("heading", {
      name: "This day does not have a published profile yet."
    })
  ).toBeVisible();
});
