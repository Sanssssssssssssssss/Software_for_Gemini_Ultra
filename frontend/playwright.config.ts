import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import path from "node:path";

const frontendRoot = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(frontendRoot, "..");

export default defineConfig({
  testDir: path.join(frontendRoot, "e2e"),
  timeout: 30_000,
  fullyParallel: false,
  retries: 0,
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: path.join(repoRoot, "output", "playwright", "report") }],
  ],
  outputDir: path.join(repoRoot, "output", "playwright", "test-results"),
  use: {
    baseURL: "http://127.0.0.1:8011",
    headless: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: {
    command:
      'powershell -NoProfile -Command "Get-ChildItem -Path \'.\\\\data\' -Filter \'gemini_service.e2e.db*\' -ErrorAction SilentlyContinue | Remove-Item -Force; & \'.\\\\.venv\\\\Scripts\\\\python.exe\' \'scripts/run_local.py\' --env-file \'config/e2e.mock.env\' --host 127.0.0.1 --port 8011"',
    url: "http://127.0.0.1:8011/healthz",
    cwd: repoRoot,
    timeout: 120_000,
    reuseExistingServer: false,
  },
});
