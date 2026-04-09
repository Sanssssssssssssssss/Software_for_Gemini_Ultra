import fs from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..", "..");
const outputDir = path.join(repoRoot, "output", "perf");
const tracePath = path.join(outputDir, "chat-stream-trace.json");
const summaryPath = path.join(outputDir, "chat-stream-summary.json");
const logPath = path.join(outputDir, "chat-stream-trace.log");
const baseUrl = process.env.GEMINI_FRONTEND_BASE_URL ?? "http://127.0.0.1:8011";
const streamDelayMs = Number(process.env.GEMINI_STREAM_DELAY_MS ?? "1600");
const traceDurationMs = Number(process.env.GEMINI_TRACE_DURATION_MS ?? "12000");

function parseTrace(trace) {
  const events = Array.isArray(trace.traceEvents) ? trace.traceEvents : [];
  const longTasks = events
    .filter((event) => event.name === "RunTask" && typeof event.dur === "number" && event.dur > 50_000)
    .map((event) => Number((event.dur / 1000).toFixed(2)));
  const layoutEvents = events.filter((event) => event.name === "Layout" && typeof event.dur === "number");
  const styleEvents = events.filter(
    (event) =>
      (event.name === "UpdateLayoutTree" || event.name === "RecalculateStyles") &&
      typeof event.dur === "number",
  );

  return {
    longTaskCount: longTasks.length,
    maxLongTaskMs: longTasks.length ? Math.max(...longTasks) : 0,
    layoutEventCount: layoutEvents.length,
    maxLayoutMs: layoutEvents.length ? Number((Math.max(...layoutEvents.map((item) => item.dur)) / 1000).toFixed(2)) : 0,
    styleEventCount: styleEvents.length,
    maxStyleMs: styleEvents.length ? Number((Math.max(...styleEvents.map((item) => item.dur)) / 1000).toFixed(2)) : 0,
  };
}

async function waitForFile(filePath, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (existsSync(filePath)) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Trace file was not created in time: ${filePath}`);
}

async function log(message) {
  await fs.appendFile(logPath, `${new Date().toISOString()} ${message}\n`, "utf8");
}

async function main() {
  await fs.mkdir(outputDir, { recursive: true });
  await fs.writeFile(logPath, "", "utf8");
  await log("start");
  if (existsSync(tracePath)) {
    await fs.rm(tracePath, { force: true });
  }

  const browser = await chromium.launch({
    headless: true,
    args: [
      "--trace-startup",
      "--trace-startup-format=json",
      `--trace-startup-file=${tracePath}`,
      `--trace-startup-duration=${Math.ceil(traceDurationMs / 1000)}`,
      "--trace-startup-categories=devtools.timeline,disabled-by-default-devtools.timeline,blink.user_timing,v8.execute",
    ],
  });
  await log("browser launched");
  const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  const page = await context.newPage();
  await log("page created");

  await page.route("**/ui/api/messages:stream", async (route) => {
    const request = route.request().postDataJSON();
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
        message: "Streaming reply",
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
        message_id: `mock-message-${Date.now()}`,
        user_message_id: null,
        gemini_metadata: [],
        created_at: new Date().toISOString(),
      })}\n\n`,
    ].join("");

    await page.waitForTimeout(streamDelayMs);
    await route.fulfill({
      body: payload,
      contentType: "text/event-stream",
      status: 200,
    });
  });

  await page.addInitScript(() => {
    const perfAudit = {
      messageMutations: 0,
      railMutations: 0,
      longTasks: [],
    };
    window.__perfAudit = perfAudit;

    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        perfAudit.longTasks.push({
          duration: entry.duration,
          name: entry.name,
        });
      }
    }).observe({ entryTypes: ["longtask"] });

  });

  await page.goto(`${baseUrl}/ui/login`, { waitUntil: "networkidle" });
  await log("login page loaded");
  await page.getByTestId("login-username").fill("admin");
  await page.getByTestId("login-password").fill("e2e-admin-pass");
  await page.getByTestId("login-submit").click();
  await page.waitForURL("**/ui/chat");
  await log("chat page loaded");
  await page.evaluate(() => {
    const perfAudit = window.__perfAudit;
    const rail = document.querySelector(".session-rail-list");
    const messages = document.querySelector("[data-testid='chat-messages']");
    if (rail) {
      new MutationObserver(() => {
        perfAudit.railMutations += 1;
      }).observe(rail, { childList: true, subtree: true, characterData: true });
    }
    if (messages) {
      new MutationObserver(() => {
        perfAudit.messageMutations += 1;
      }).observe(messages, { childList: true, subtree: true, characterData: true });
    }
  });

  const railItems = page.locator(".session-rail-item");
  let railCount = await railItems.count();
  await page.getByTestId("new-session-button").click();
  await railItems.nth(railCount).waitFor({ state: "visible" });
  await page.waitForFunction(
    (index) => document.querySelectorAll(".session-rail-item")[index]?.classList.contains("active"),
    railCount,
  );
  railCount += 1;
  await log("session A created");
  await page.getByTestId("chat-composer").fill("Performance trace turn A");
  await page.waitForFunction(() => !document.querySelector("[data-testid='chat-send']")?.hasAttribute("disabled"));
  await page.getByTestId("chat-send").click();
  await log("session A send started");
  await page.getByTestId("new-session-button").click();
  await railItems.nth(railCount).waitFor({ state: "visible" });
  await page.waitForFunction(
    (index) => document.querySelectorAll(".session-rail-item")[index]?.classList.contains("active"),
    railCount,
  );
  railCount += 1;
  await log("session B created");
  await page.getByTestId("chat-composer").fill("Performance trace turn B");
  const secondSendEnabledDuringPending = await page.getByTestId("chat-send").isEnabled();
  await page.waitForFunction(() => !document.querySelector("[data-testid='chat-send']")?.hasAttribute("disabled"));
  await page.getByTestId("chat-send").click();
  await log(`second send enabled: ${secondSendEnabledDuringPending}`);
  await page.waitForFunction(
    () => document.body.textContent?.includes("[mock-ready-1|healthy] Performance trace turn B"),
    null,
    { timeout: 15_000 },
  );
  await log("second reply visible");

  const mutationSummary = await page.evaluate(() => ({
    longTaskEntries: (window.__perfAudit?.longTasks ?? []).filter((entry) => entry.duration > 50).length,
    messageMutations: window.__perfAudit?.messageMutations ?? 0,
    railMutations: window.__perfAudit?.railMutations ?? 0,
    reactDevtoolsHookPresent: Boolean(window.__REACT_DEVTOOLS_GLOBAL_HOOK__),
  }));

  await page.waitForTimeout(Math.max(traceDurationMs - 3000, 1500));
  await log("trace wait complete");
  await browser.close();
  await log("browser closed");
  await waitForFile(tracePath, 10_000);
  await log("trace file present");

  const trace = JSON.parse(await fs.readFile(tracePath, "utf8"));
  const parsed = parseTrace(trace);
  const summary = {
    ...parsed,
    ...mutationSummary,
    baseUrl,
    secondSendEnabledDuringPending,
    streamDelayMs,
    traceDurationMs,
  };

  await fs.writeFile(summaryPath, JSON.stringify(summary, null, 2), "utf8");
  await log("summary written");
  console.log(JSON.stringify(summary, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
