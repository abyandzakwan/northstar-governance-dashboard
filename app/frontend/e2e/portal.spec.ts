import { test, expect } from "@playwright/test";

test("native Cognito sign-in starts PKCE without a SAML provider", async ({
  page,
}) => {
  await page.route("**/config.json", (route) =>
    route.fulfill({
      json: {
        apiUrl: "/api",
        cognitoDomain: "https://login.example.invalid",
        clientId: "test-client",
        redirectUri: "http://127.0.0.1:5173",
        identityProvider: null,
      },
    }),
  );
  await page.route("**/api/session", (route) =>
    route.fulfill({ status: 401, json: { message: "Unauthorized" } }),
  );
  await page.route("https://login.example.invalid/**", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: "<h1>Test login destination</h1>",
    }),
  );
  await page.goto("/");
  await expect(
    page.getByText("Request failed (401)", { exact: true }),
  ).not.toBeVisible();
  await page.getByRole("button", { name: "Sign in to portal" }).click();
  await page.waitForURL("https://login.example.invalid/oauth2/authorize?**");
  const params = new URL(page.url()).searchParams;
  expect(params.get("code_challenge_method")).toBe("S256");
  expect(params.get("code_challenge")).toHaveLength(43);
  expect(params.get("state")).toBeTruthy();
  expect(params.has("identity_provider")).toBe(false);
});
test("overview, filters, account evidence, framework drill-down and reports work", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Organization overview" }),
  ).toBeVisible();
  await expect(
    page.getByText("Interactive demo", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".metric strong").nth(1)).toHaveText("37");
  await page.screenshot({
    path: "test-results/overview-desktop.png",
    fullPage: true,
  });
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "AWS accounts", exact: true })
    .click();
  await page
    .getByLabel("Environment", { exact: true })
    .selectOption("production");
  await page.getByLabel("Search accounts").fill("core-platform");
  await expect(
    page.getByRole("button", { name: "core-platform-production" }).first(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "core-platform-production" })
    .first()
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Resource findings" }),
  ).toBeVisible();
  await page.getByLabel("Finding status").selectOption("all");
  await expect(
    page.getByText("CloudTrail enabled in every Region").first(),
  ).toBeVisible();
  await page.getByLabel("Close account detail").click();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "Frameworks", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: /CIS AWS Foundations/ }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "ISO/IEC 27001", exact: true })
    .click();
  await expect(page.getByText(/not certification/)).toBeVisible();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "Reports & exports", exact: true })
    .click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Generate & download" }).click();
  expect((await download).suggestedFilename()).toMatch(/governance.*csv/);
  expect(errors).toEqual([]);
});
test("mobile navigation and report settings remain usable", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Organization overview" }),
  ).toBeVisible();
  await page.getByLabel("Toggle navigation").click();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "Reports & exports" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Build an audit export" }),
  ).toBeVisible();
  await page.getByLabel("Enable weekly summaries").check();
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByRole("status")).toContainText("settings saved");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/reports-mobile.png",
    fullPage: true,
  });
});

test("production waiver validation, approval and revocation work", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator(".metric strong").nth(1)).toHaveText("37");
  const response = await page.request.get("/api/accounts?env=production");
  const accounts = (await response.json()).items;
  const account = accounts.find(
    (a: { fail_count: number }) => a.fail_count > 0,
  );
  const findingsResponse = await page.request.get(
    `/api/accounts/${account.id}/findings?status=FAIL`,
  );
  const finding = (await findingsResponse.json()).items[0];
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "Risk exceptions", exact: true })
    .click();
  await page.getByRole("button", { name: "Create exception" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("AWS account").selectOption(account.id);
  await dialog
    .getByRole("combobox", { name: "Rule", exact: true })
    .selectOption(finding.rule_id);
  const owner = `browser-test-${Date.now()}@example.com`;
  await dialog.getByLabel("Risk owner").fill(owner);
  await dialog
    .getByLabel("Reason")
    .fill("Migration exception approved during browser validation.");
  await dialog
    .getByLabel("Expiry date")
    .fill(new Date(Date.now() + 91 * 86400000).toISOString().slice(0, 16));
  await dialog.getByRole("button", { name: "Submit exception" }).click();
  await expect(dialog.getByRole("alert")).toContainText("within 90 days");
  await dialog
    .getByLabel("Expiry date")
    .fill(new Date(Date.now() + 86400000).toISOString().slice(0, 16));
  await dialog.getByRole("button", { name: "Submit exception" }).click();
  await expect(dialog).not.toBeVisible();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "Remediation & changes" })
    .click();
  await page
    .getByRole("button", { name: "Simulate approved PR" })
    .first()
    .click();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "Risk exceptions", exact: true })
    .click();
  const row = page.getByRole("row").filter({ hasText: owner });
  await expect(row).toContainText("Accepted risk");
  await row.getByRole("button", { name: "Revoke" }).click();
  await expect(row).toContainText("Accepted risk");
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "Remediation & changes" })
    .click();
  await page
    .getByRole("button", { name: "Simulate approved PR" })
    .first()
    .click();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "Risk exceptions", exact: true })
    .click();
  await expect(page.getByRole("row").filter({ hasText: owner })).toContainText(
    "Revoked",
  );
});

test("multi-account remediation gates production and recovers failed native targets", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "Remediation & changes" })
    .click();
  await expect(
    page.getByText("Workflow demonstration", { exact: false }),
  ).toBeVisible();
  const response = await page.request.get("/api/accounts");
  const accounts = (await response.json()).items;
  for (const environment of ["dev", "production"]) {
    const account = accounts.find(
      (a: { name: string; environment: string }) =>
        a.environment === environment && a.name.startsWith("core-platform"),
    );
    await page.getByLabel("Remediation account").selectOption(account.id);
    await page
      .getByRole("checkbox", { name: /Select SECURITY_HUB::S3.14/ })
      .check();
  }
  await page
    .getByRole("checkbox", { name: "Simulate first deployment failure" })
    .check();
  const prepared = page.waitForResponse(
    (r) => r.url().endsWith("/api/changes") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Prepare 2 selected" }).click();
  const jobs = (await (await prepared).json()).items;
  const dev = jobs.find(
    (j: { environment: string }) => j.environment === "dev",
  );
  const prod = jobs.find(
    (j: { environment: string }) => j.environment === "production",
  );
  const devCard = page
    .locator(".change-card")
    .filter({ hasText: dev.id.slice(0, 8) });
  const prodCard = page
    .locator(".change-card")
    .filter({ hasText: prod.id.slice(0, 8) });
  await devCard.getByRole("button", { name: "Execute change" }).click();
  await expect(devCard.getByText("failed", { exact: true })).toBeVisible();
  await devCard.getByRole("button", { name: "Retry failed targets" }).click();
  await expect(devCard.getByText("succeeded", { exact: true })).toBeVisible();
  await prodCard.getByRole("button", { name: "Request production PR" }).click();
  await expect(
    prodCard.getByText("awaiting approval", { exact: true }),
  ).toBeVisible();
  const pending = await page.request.get(`/api/changes/${prod.id}`);
  expect((await pending.json()).outcomes).toEqual({});
  await prodCard.getByRole("button", { name: "Simulate approved PR" }).click();
  await expect(prodCard.getByText("failed", { exact: true })).toBeVisible();
  await prodCard.getByRole("button", { name: "Retry failed targets" }).click();
  await expect(prodCard.getByText("succeeded", { exact: true })).toBeVisible();
  await page.screenshot({
    path: "test-results/changes-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/changes-mobile.png",
    fullPage: true,
  });
});
