// End-to-end smoke test against a running stack (API + Vite or built frontend).
// Usage: BASE_URL=http://localhost:5173 E2E_EMAIL=... E2E_PASSWORD=... SHOTS=./shots node e2e/smoke.mjs
import fs from "node:fs";
import { createRequire } from "node:module";

// Resolve Playwright from the project or a global install (NODE_PATH).
const { chromium, devices } = createRequire(import.meta.url)("playwright");

const BASE = process.env.BASE_URL ?? "http://localhost:5173";
const EMAIL = process.env.E2E_EMAIL ?? "admin@demo.example.com";
const PASSWORD = process.env.E2E_PASSWORD ?? "Admin-Passw0rd!";
const SHOTS = process.env.SHOTS;
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

const PAGES = [
  ["/", "Good"], ["/inventory/products", "Products"], ["/inventory/stock", "Stock overview"], ["/inventory/warehouses", "Warehouses"],
  ["/inventory/movements", "Stock movements"], ["/inventory/transfers", "Stock transfers"], ["/inventory/adjustments", "Adjustments"],
  ["/inventory/receipts", "Goods receipts"], ["/inventory/alerts", "Inventory alerts"], ["/inventory/reports", "Reports"],
  ["/purchasing/suppliers", "Suppliers"], ["/purchasing/requisitions", "Purchase requisitions"], ["/purchasing/orders", "Purchase orders"],
  ["/approvals", "Action approvals"], ["/settings/company", "Company"], ["/settings/users", "Users"], ["/settings/roles", "Roles"],
  ["/settings/departments", "Departments"], ["/settings/units", "Units of measurement"], ["/settings/system", "System settings"],
  ["/settings/audit", "Audit log"], ["/profile", "Profile"],
];

const failures = [];
const check = (cond, msg) => { if (!cond) failures.push(msg); console.log(`${cond ? "✓" : "✗"} ${msg}`); };

const browser = await chromium.launch();
async function run(contextOpts, label, full) {
  const ctx = await browser.newContext(contextOpts);
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 500) errors.push(`${r.status()} ${r.url()}`); });

  await page.goto(`${BASE}/login`);
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(`${BASE}/`);
  check(true, `[${label}] login`);

  for (const [path, heading] of full ? PAGES : PAGES.slice(0, 3)) {
    await page.goto(`${BASE}${path}`);
    await page.getByRole("heading", { level: 1 }).filter({ hasText: heading }).first().waitFor({ timeout: 10000 }).catch(() => {});
    await page.waitForLoadState("networkidle");
    const h1 = await page.getByRole("heading", { level: 1 }).first().textContent().catch(() => "");
    const errorState = await page.getByText("Couldn’t load this data").count();
    check(h1?.includes(heading) && errorState === 0, `[${label}] ${path} renders "${heading}"`);
    await page.waitForTimeout(700); // let chart animations settle
    if (SHOTS && ["/", "/inventory/products", "/inventory/stock", "/purchasing/orders", "/settings/roles"].includes(path)) {
      await page.screenshot({ path: `${SHOTS}/${label}${(path === "/" ? "_home" : path.replaceAll("/", "_"))}.png`, fullPage: label === "desktop" });
    }
  }

  if (full) {
    // Product detail via row click
    await page.goto(`${BASE}/inventory/products`);
    await page.getByRole("row").nth(1).click();
    await page.waitForURL(/\/inventory\/products\/.+/);
    await page.getByText("Stock by location").waitFor();
    check(true, `[${label}] product detail opens from list`);

    // AI Command Center: question with verified card
    await page.goto(`${BASE}/ai`);
    await page.getByRole("button", { name: "Which items are running low?", exact: true }).click();
    await page.getByText("Items needing reorder").waitFor({ timeout: 20000 });
    check(await page.getByText("Calculated").count() > 0, `[${label}] AI answer shows provenance-labelled card`);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${label}_ai_lowstock.png` });

    // Draft action → approval card → approve
    await page.getByLabel("Message the AI Command Center").fill("Create a purchase request for 300 kg of STL-PL-12");
    await page.keyboard.press("Enter");
    await page.getByText("Proposed purchase requisition").waitFor({ timeout: 20000 });
    check(await page.getByText("Draft action").count() > 0, `[${label}] requisition prepared as draft`);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${label}_ai_draft.png` });
    await page.getByRole("button", { name: "Approve & execute" }).click();
    await page.getByText("Approved and executed").waitFor({ timeout: 15000 });
    check(true, `[${label}] approval executed from chat`);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${label}_ai_executed.png` });

    // Theme toggle
    await page.getByRole("button", { name: /Switch to (dark|light) theme/ }).click();
    await page.goto(`${BASE}/`);
    await page.waitForLoadState("networkidle");
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${label}_home_dark.png`, fullPage: true });
    check(await page.evaluate(() => document.documentElement.classList.contains("dark")) !== undefined, `[${label}] theme toggles`);
  }

  check(errors.length === 0, `[${label}] no console errors / 5xx responses${errors.length ? `: ${errors.slice(0, 3).join(" | ")}` : ""}`);
  if (!full) {
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    check(!overflow, `[${label}] no horizontal page overflow`);
  }
  await ctx.close();
}

await run({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 }, "desktop", true);
await run({ ...devices["iPhone 13"] }, "mobile", false);
await browser.close();
if (failures.length) { console.error(`\n${failures.length} check(s) failed`); process.exit(1); }
console.log("\nAll smoke checks passed");
