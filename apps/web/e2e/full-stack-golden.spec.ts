import { expect, test } from "@playwright/test";

test("serves the reviewed golden artifact through the complete runtime path", async ({
  page
}) => {
  test.skip(
    process.env.DAY_PERSPECTIVE_FULL_STACK !== "1",
    "Run through make web-e2e-full-stack with FastAPI and a published fixture."
  );

  await page.goto("/day/1964-03-27");
  await expect(page.getByText("USGS reports a magnitude of 9.2 Mw.")).toBeVisible();
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

  const qualityCard = page
    .getByRole("heading", { name: "Evidence notes" })
    .locator("..");
  await qualityCard.getByText("Why can the app say this?").click();
  await expect(qualityCard.getByText(/Derived value/)).toBeVisible();
  await expect(qualityCard.getByText(/calculation version 0.3.0/)).toBeVisible();
});
