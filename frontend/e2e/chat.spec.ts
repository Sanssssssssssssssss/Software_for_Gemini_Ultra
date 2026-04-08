import { expect, test, type Page } from "@playwright/test";

const PNG_BUFFER = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYAAAAAQAAQunAp0AAAAASUVORK5CYII=",
  "base64",
);

const PDF_BUFFER = Buffer.from("%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n", "utf8");

async function loginAsAdmin(page: Page) {
  await page.goto("/ui/login");
  await page.getByTestId("login-username").fill("admin");
  await page.getByTestId("login-password").fill("e2e-admin-pass");
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/ui\/chat$/);
}

async function startPinnedSession(page: Page) {
  await page.getByTestId("new-session-button").click();
  await expect(page.locator("body")).toContainText("Sticky account: mock-ready-1", { timeout: 15000 });
}

test("text-only streaming regression still works", async ({ page }) => {
  await loginAsAdmin(page);
  await startPinnedSession(page);

  await page.getByTestId("chat-composer").fill("Please reply MOCK_OK");
  await page.getByTestId("chat-send").click();

  await expect(page.getByTestId("chat-messages")).toContainText("[mock-ready-1|healthy] Please reply MOCK_OK", {
    timeout: 15000,
  });
  await expect(page.locator("body")).toContainText("Streaming response completed.", { timeout: 15000 });
});

test("image upload can be staged and sent with a prompt", async ({ page }) => {
  await loginAsAdmin(page);
  await startPinnedSession(page);

  await page.locator('input[type="file"]').setInputFiles({
    name: "sample.png",
    mimeType: "image/png",
    buffer: PNG_BUFFER,
  });

  await expect(page.getByTestId("upload-queue")).toContainText("Ready to send", { timeout: 15000 });
  await page.getByTestId("chat-composer").fill("Please review the attached file");
  await page.getByTestId("chat-send").click();

  await expect(page.getByTestId("chat-messages")).toContainText("files=1", { timeout: 15000 });
});

test("pdf upload can be staged and sent", async ({ page }) => {
  await loginAsAdmin(page);
  await startPinnedSession(page);

  await page.locator('input[type="file"]').setInputFiles({
    name: "sample.pdf",
    mimeType: "application/pdf",
    buffer: PDF_BUFFER,
  });

  await expect(page.getByTestId("upload-queue")).toContainText("Ready to send", { timeout: 15000 });
  await page.getByTestId("chat-composer").fill("Summarize the PDF");
  await page.getByTestId("chat-send").click();

  await expect(page.getByTestId("chat-messages")).toContainText("files=1", { timeout: 15000 });
});

test("unsupported upload shows a recoverable error", async ({ page }) => {
  await loginAsAdmin(page);
  await startPinnedSession(page);

  await page.locator('input[type="file"]').setInputFiles({
    name: "notes.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("hello"),
  });

  await expect(page.getByTestId("upload-queue")).toContainText("Unsupported or unrecognized file format", {
    timeout: 15000,
  });
  await page.getByTestId("chat-composer").fill("This should still be editable");
  await expect(page.getByTestId("chat-composer")).toHaveValue("This should still be editable");
});
