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
  const railItems = page.locator(".session-rail-item");
  const beforeCount = await railItems.count();
  await page.getByTestId("new-session-button").click();
  await expect(railItems).toHaveCount(beforeCount + 1, { timeout: 15000 });
  await expect(page.getByTestId("chat-composer")).toBeVisible();
}

async function mockDelayedStreaming(page: Page, delayMs = 1500) {
  let streamCount = 0;

  await page.route("**/ui/api/messages:stream", async (route) => {
    const request = route.request().postDataJSON() as {
      message?: string | null;
      parts?: Array<{ type: "text"; text?: string } | { type: "asset"; asset_id: string }>;
      session_id: string;
    };
    const textPart = request.parts?.find((part) => part.type === "text");
    const attachmentCount = request.parts?.filter((part) => part.type === "asset").length ?? 0;
    const prompt = request.message || textPart?.text || (attachmentCount ? `files=${attachmentCount}` : "");
    const reply = `[mock-ready-1|healthy] ${prompt}`;
    const payload = [
      `event: accepted\ndata: ${JSON.stringify({
        session_id: request.session_id,
        account_id: "mock-ready-1",
      })}\n\n`,
      `event: status\ndata: ${JSON.stringify({
        session_id: request.session_id,
        account_id: "mock-ready-1",
        phase: "streaming",
        message: "正在生成回复",
      })}\n\n`,
      `event: chunk\ndata: ${JSON.stringify({
        session_id: request.session_id,
        account_id: "mock-ready-1",
        text_delta: reply,
      })}\n\n`,
      `event: done\ndata: ${JSON.stringify({
        session_id: request.session_id,
        account_id: "mock-ready-1",
        content: reply,
        parts: [{ type: "text", text: reply }],
        media: [],
        cached: false,
        message_id: `mock-message-${streamCount}`,
        user_message_id: null,
        gemini_metadata: [],
        created_at: new Date().toISOString(),
      })}\n\n`,
    ].join("");

    streamCount += 1;
    await page.waitForTimeout(delayMs);
    await route.fulfill({
      body: payload,
      contentType: "text/event-stream",
      status: 200,
    });
  });
}

test("text-only streaming regression still works", async ({ page }) => {
  await loginAsAdmin(page);
  await startPinnedSession(page);

  await page.getByTestId("chat-composer").fill("Please reply MOCK_OK");
  await page.getByTestId("chat-send").click();

  await expect(page.getByTestId("chat-messages")).toContainText("[mock-ready-1|healthy] Please reply MOCK_OK", {
    timeout: 15000,
  });
  await expect(page.locator("body")).toContainText("流式回复已完成。", { timeout: 15000 });
});

test("image upload can be staged and sent with a prompt", async ({ page }) => {
  await loginAsAdmin(page);
  await startPinnedSession(page);

  await page.locator('input[type="file"]').setInputFiles({
    name: "sample.png",
    mimeType: "image/png",
    buffer: PNG_BUFFER,
  });

  await expect(page.getByTestId("upload-queue")).toContainText("待发送", { timeout: 15000 });
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

  await expect(page.getByTestId("upload-queue")).toContainText("待发送", { timeout: 15000 });
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

test("another session can be created, browsed, and sent while a previous request is pending", async ({ page }) => {
  await mockDelayedStreaming(page);
  await loginAsAdmin(page);
  await startPinnedSession(page);
  const initialCount = await page.locator(".session-rail-item").count();

  await page.getByTestId("chat-composer").fill("First turn stays pending");
  await page.getByTestId("chat-send").click();

  await page.getByTestId("new-session-button").click();
  await expect(page.locator(".session-rail-item")).toHaveCount(initialCount + 1);

  await page.getByTestId("chat-composer").fill("Second turn can still send");
  await expect(page.getByTestId("chat-send")).toBeEnabled();
  await page.getByTestId("chat-send").click();

  await page.locator(".session-rail-item").nth(1).click();
  await expect(page.getByTestId("chat-messages")).toContainText("First turn stays pending");

  await page.locator(".session-rail-item").nth(0).click();
  await expect(page.getByTestId("chat-messages")).toContainText("[mock-ready-1|healthy] Second turn can still send", {
    timeout: 10000,
  });
});

test("session can be renamed and deleted from the rail", async ({ page }) => {
  await loginAsAdmin(page);
  await startPinnedSession(page);

  await page.getByTestId("chat-composer").fill("这是一个需要改名的会话");
  await page.getByTestId("chat-send").click();
  await expect(page.locator(".session-rail-item").first()).toContainText("这是一个需要改名的会话", {
    timeout: 15000,
  });

  const initialCount = await page.locator(".session-rail-item").count();
  await page.locator(".session-rail-item__menu").first().click();
  await page.getByRole("button", { name: "重命名" }).click();
  await page.locator(".session-rail-item__title-input").fill("新的会话标题");
  await page.locator(".session-rail-item__title-input").press("Enter");
  await expect(page.locator(".session-rail-item").first()).toContainText("新的会话标题");

  page.once("dialog", (dialog) => dialog.accept());
  await page.locator(".session-rail-item__menu").first().click();
  await page.getByRole("button", { name: "删除" }).click();
  await expect(page.locator(".session-rail-item")).toHaveCount(initialCount - 1);
});
