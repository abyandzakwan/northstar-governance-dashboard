import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: process.env.PORTAL_E2E_URL || "http://127.0.0.1:5190",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: process.env.PORTAL_E2E_URL
    ? undefined
    : [
        {
          command:
            "PYTHONPATH=app/functions PORTAL_MODE=demo LOCAL_STATE=:memory: LOCAL_PORT=8190 python -m portal.local",
          cwd: "../..",
          url: "http://127.0.0.1:8190/api/environments",
          reuseExistingServer: false,
        },
        {
          command:
            "PORTAL_API_URL=http://127.0.0.1:8190 npm run dev -- --port 5190 --strictPort",
          url: "http://127.0.0.1:5190",
          reuseExistingServer: false,
        },
      ],
});
