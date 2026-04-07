import { expect, test } from "@playwright/test";

test("chat streaming stays within the modern composer flow", async ({ page }) => {
  await page.goto("/ui/login");

  await page.getByTestId("login-username").fill("admin");
  await page.getByTestId("login-password").fill("e2e-admin-pass");
  await page.getByTestId("login-submit").click();

  await expect(page).toHaveURL(/\/ui\/chat$/);
  await page.getByTestId("chat-composer").fill("请只回复 MOCK_OK");
  await page.getByTestId("chat-send").click();

  await expect(page.getByTestId("chat-messages")).toContainText("MOCK_OK", { timeout: 15000 });
  await expect(page.locator("body")).toContainText("Streaming response completed.", { timeout: 15000 });
});
