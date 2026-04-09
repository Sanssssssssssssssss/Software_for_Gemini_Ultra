import { expect, test } from "@playwright/test";

test("login page leads into the modern chat workspace", async ({ page }) => {
  await page.goto("/ui/login");

  await page.getByTestId("login-username").fill("admin");
  await page.getByTestId("login-password").fill("e2e-admin-pass");
  await page.getByTestId("login-submit").click();

  await expect(page).toHaveURL(/\/ui\/chat$/);
  await expect(page.locator(".chat-topbar")).toBeVisible();
  await expect(page.getByTestId("new-session-button")).toBeVisible();
});

test("standard user is redirected away from admin", async ({ page }) => {
  await page.goto("/ui/login");

  await page.getByTestId("login-username").fill("analyst");
  await page.getByTestId("login-password").fill("e2e-user-pass");
  await page.getByTestId("login-submit").click();

  await expect(page).toHaveURL(/\/ui\/chat$/);
  const response = await page.goto("/admin");
  expect(response?.status()).toBe(403);
  await expect(page.locator("body")).toContainText("Administrator access is required");
});

test("responsive workspace shell stays usable on a common laptop viewport", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto("/ui/login");

  await page.getByTestId("login-username").fill("admin");
  await page.getByTestId("login-password").fill("e2e-admin-pass");
  await page.getByTestId("login-submit").click();

  await expect(page).toHaveURL(/\/ui\/chat$/);
  await expect(page.getByTestId("new-session-button")).toBeVisible();
  await expect(page.getByTestId("chat-composer")).toBeVisible();
  await expect(page.getByTestId("chat-send")).toBeVisible();
});
