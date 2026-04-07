import { expect, test } from "@playwright/test";

test("admin overview performs async account actions without leaving the page", async ({ page }) => {
  await page.goto("/ui/login");

  await page.getByTestId("login-username").fill("admin");
  await page.getByTestId("login-password").fill("e2e-admin-pass");
  await page.getByTestId("login-submit").click();

  await page.goto("/admin");
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByRole("heading", { name: "Account pool admin" })).toBeVisible();

  const refreshButton = page.getByTestId("admin-action-mock-ready-1-refresh");
  await refreshButton.click();

  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByText("mock-ready-1: Account refreshed.")).toBeVisible();
});
