// Exercise the deployed CSP, API routes and OAuth redirect that local Vite tests cannot cover.
import { readFileSync } from "node:fs";
import { chromium } from "../app/frontend/node_modules/playwright-core/index.mjs";

const output = JSON.parse(readFileSync("build/outputs.json", "utf8"));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (/violates.*Content Security Policy|contains an invalid source/.test(message.text())) {
      errors.push(message.text());
    }
  });
  const ready = page.waitForResponse((response) => response.url() === output.frontend_config.apiUrl + "/session");
  await page.goto(output.portal_url);
  if ((await ready).status() !== 401) throw new Error("Unauthenticated session must be rejected");
  const statuses = await page.evaluate(async (apiUrl) => {
    const results = {};
    for (const path of ["/frameworks", "/changes"]) {
      results[path] = (await fetch(apiUrl + path)).status;
    }
    return results;
  }, output.frontend_config.apiUrl);
  if (Object.values(statuses).some((status) => status !== 401)) {
    throw new Error("Missing or unprotected application routes: " + JSON.stringify(statuses));
  }
  await page.getByRole("button", { name: "Sign in to portal" }).click();
  await page.locator('input[name="username"]:visible').waitFor();
  await page.locator('input[name="password"]:visible').waitFor();
  if (new URL(page.url()).origin !== output.frontend_config.cognitoDomain) throw new Error("Unexpected login origin");
  if (errors.length) throw new Error(errors.join("; "));
  console.log("Live browser smoke passed: CSP, protected API routes, and Cognito sign-in.");
} finally {
  await browser.close();
}
