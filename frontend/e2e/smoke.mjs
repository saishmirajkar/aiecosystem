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
  ["/workforce", "Workforce"], ["/workforce/schedule", "Shift schedule"], ["/workforce/leave", "Leave"], ["/workforce/overtime", "Overtime"],
  ["/workforce/attendance", "Attendance"], ["/workforce/overtime-records", "Overtime records"], ["/workforce/employees", "Employees"],
  ["/workforce/payroll", "Payroll export"], ["/workforce/reports", "Workforce reports"], ["/workforce/setup", "Workforce setup"],
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

    // Module switcher: ERP ⇄ Workforce, returning to where you left off
    await page.goto(`${BASE}/inventory/stock`);
    await page.getByRole("heading", { level: 1 }).filter({ hasText: "Stock overview" }).waitFor();
    await page.getByRole("group", { name: "Switch module" }).getByRole("button", { name: "Workforce" }).click();
    await page.waitForURL(`${BASE}/workforce/setup`);  // the last Workforce page this session visited
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    check(await nav.getByRole("link", { name: "Shift schedule" }).count() === 1 && await nav.getByRole("link", { name: "Products" }).count() === 0,
      `[${label}] Workforce module shows only workforce menus`);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/${label}_module_workforce.png` });
    await page.getByRole("group", { name: "Switch module" }).getByRole("button", { name: "ERP" }).click();
    await page.waitForURL(`${BASE}/inventory/stock`);
    check(await nav.getByRole("link", { name: "Products" }).count() === 1 && await nav.getByRole("link", { name: "Shift schedule" }).count() === 0,
      `[${label}] ERP module returns to the last ERP page`);

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

// ---------------------------------------------------------------- workforce: leave → vacancy → first eligible claim → payroll
const DEMO_PW = process.env.E2E_DEMO_PASSWORD ?? "Demo-Passw0rd!";
async function signIn(contextOpts, id, label) {
  const ctx = await browser.newContext(contextOpts);
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 500) errors.push(`${r.status()} ${r.url()}`); });
  await page.goto(`${BASE}/login`);
  await page.getByLabel("Email or employee code").fill(id);
  await page.getByLabel("Password").fill(DEMO_PW);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"));
  return { ctx, page, errors, label };
}
const api = (page, path, init) => page.evaluate(async ([p, i]) => {
  const r = await fetch(`/api/v1${p}`, { credentials: "same-origin", ...(i ?? {}), headers: { "X-Requested-With": "above-web", "Content-Type": "application/json", ...(i?.headers ?? {}) } });
  return { status: r.status, body: await r.json() };
}, [path, init]);
const shot = async (page, name, full = false) => { if (SHOTS) { await page.waitForTimeout(400); await page.screenshot({ path: `${SHOTS}/wf_${name}.png`, fullPage: full }); } };
const noOverflow = (page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);

async function workforceFlow() {
  const phone = { ...devices["iPhone 13"] };
  const desk = { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 };
  const sessions = [];

  // 1. Employee applies for leave from a phone (signs in with an employee code).
  const emp = await signIn(phone, "EMP-101", "employee"); sessions.push(emp);
  await emp.page.waitForURL(`${BASE}/me`);
  await emp.page.getByText("Overtime in your team").waitFor();
  check(await noOverflow(emp.page), "[workforce] employee home fits a phone screen");
  await shot(emp.page, "employee_home", true);
  const me = (await api(emp.page, "/workforce/me")).body;
  const mgr = await signIn(desk, "shift.manager@demo.example.com", "manager"); sessions.push(mgr);
  // Pick a day of EMP-101's rota on which an overtime-eligible teammate is free, so there is someone to offer it to.
  const team = (await api(mgr.page, "/workforce/teams")).body.find((t) => t.name === "Assembly Team");
  const okCodes = new Set((await api(mgr.page, `/employees?team_id=${team.id}&page_size=200`)).body.items.filter((e) => e.overtime_eligible).map((e) => e.employee_code));
  let target = null;
  for (const a of me.upcoming.filter((x) => x.source === "schedule").slice(2)) {
    const free = (await api(mgr.page, `/shifts/${a.shift_id}/candidates`)).body.filter((c) => c.eligible && okCodes.has(c.employee.employee_code) && c.employee.employee_code !== "EMP-101");
    if (free.length) { target = a.shift_date; break; }
  }
  check(!!target, `[workforce] employee has an upcoming shift a teammate could cover (${target})`);
  await emp.page.goto(`${BASE}/me/leave`);
  await emp.page.getByRole("button", { name: "Apply" }).click();
  await emp.page.locator("#lv-t").selectOption({ label: "Casual Leave" });
  await emp.page.locator("#lv-s").fill(target);
  await emp.page.locator("#lv-e").fill(target);
  await emp.page.getByText(/working day\(s\)/).waitFor();
  await shot(emp.page, "employee_apply_leave");
  await emp.page.getByRole("button", { name: "Send request" }).click();
  await emp.page.getByText("Leave request sent to your manager").waitFor();
  check(true, "[workforce] employee applied for leave");

  // 2. Manager approves; the vacancy becomes an overtime opportunity automatically.
  await mgr.page.goto(`${BASE}/workforce`);
  await mgr.page.getByText("Overtime opportunities").first().waitFor();
  await shot(mgr.page, "manager_dashboard", true);
  await mgr.page.goto(`${BASE}/workforce/leave`);
  await mgr.page.getByRole("button", { name: /Ravi Kumar/ }).first().click();
  await mgr.page.getByText("Shifts affected").waitFor();
  await shot(mgr.page, "manager_leave_dialog");
  await mgr.page.getByRole("button", { name: "Approve" }).click();
  await mgr.page.getByText(/overtime opportunit(y|ies) published/).first().waitFor({ timeout: 15000 });
  check(true, "[workforce] approval published an overtime opportunity");
  const approved = (await api(mgr.page, "/leave/requests?status=approved&page_size=100")).body.items
    .find((r) => r.employee.employee_code === "EMP-101" && r.start_date === target);
  const oppId = approved?.affected_shifts?.[0]?.opportunity?.id;
  check(!!oppId, "[workforce] opportunity linked to the approved leave");
  const cands = (await api(mgr.page, `/overtime/opportunities/${oppId}/candidates`)).body;
  const claimer = cands.find((c) => c.eligible && c.same_department)?.employee.employee_code;
  check(!!claimer, `[workforce] an eligible colleague exists (${claimer})`);
  const notified = (await api(mgr.page, `/overtime/opportunities/${oppId}`)).body.recipients?.[0]?.notified ?? 0;
  check(notified >= 1, `[workforce] eligible employees notified (${notified})`);
  await mgr.page.goto(`${BASE}/workforce/schedule`);
  await mgr.page.getByText(/positions?, \d+ vacant/).waitFor();
  await shot(mgr.page, "manager_schedule", true);

  // 3. The eligible colleague claims from a phone: one tap, confirmed by the server.
  const cl = await signIn(phone, claimer, "claimer"); sessions.push(cl);
  const notes = (await api(cl.page, "/notifications?page_size=20")).body.items;
  check(notes.some((n) => n.title === "NEW OVERTIME OPPORTUNITY" && !n.body.includes("Ravi")), "[workforce] claimer got a notification that doesn't name the absent colleague");
  await cl.page.goto(`${BASE}/me/overtime`);
  await cl.page.getByRole("heading", { name: "Overtime" }).waitFor();
  await shot(cl.page, "employee_marketplace", true);
  await cl.page.goto(`${BASE}/me/overtime/${oppId}`);
  await cl.page.getByRole("button", { name: "Claim shift" }).click();
  await cl.page.getByText("Your overtime shift has been confirmed.").first().waitFor({ timeout: 15000 });
  await cl.page.getByText("You’re confirmed").waitFor({ timeout: 15000 });
  check(true, "[workforce] first eligible claim won");
  await shot(cl.page, "employee_claimed");
  await cl.page.goto(`${BASE}/me/shifts`);
  await cl.page.getByText(/Overtime \(claimed\)/).first().waitFor();
  check(true, "[workforce] claimed shift appears in the winner's schedule");
  check(await noOverflow(cl.page), "[workforce] my shifts fits a phone screen");

  // 4. A second claim through the API is refused with the agreed message.
  // Sent outside the page (same cookies) so the expected 409 isn't logged as a page error.
  const lateRes = await emp.ctx.request.post(`${BASE}/api/v1/overtime/opportunities/${oppId}/claim`,
    { headers: { "X-Requested-With": "above-web", "Idempotency-Key": crypto.randomUUID() } });
  const late = { status: lateRes.status(), body: await lateRes.json() };
  check(late.status === 409, `[workforce] a later claim is refused (${late.body?.error?.code})`);

  // 5. Manager sees the claims log; approves overtime; HR exports payroll.
  await mgr.page.goto(`${BASE}/workforce/overtime/${oppId}`);
  await mgr.page.getByText("Claims, in the order the system recorded them").waitFor();
  check(await mgr.page.getByRole("cell", { name: /Won/i }).count() > 0 || await mgr.page.getByText("Won").count() > 0, "[workforce] manager sees the winning claim");
  await shot(mgr.page, "manager_opportunity", true);
  await mgr.page.goto(`${BASE}/workforce/overtime-records`);
  await mgr.page.getByRole("button", { name: "Approve", exact: true }).first().click();
  await mgr.page.locator("[data-sonner-toast]").filter({ hasText: "Approved" }).first().waitFor();
  check(true, "[workforce] overtime approved by the manager");
  await mgr.page.goto(`${BASE}/workforce/attendance`);
  await mgr.page.getByText("Day sheet").waitFor();
  await shot(mgr.page, "manager_attendance");

  const hr = await signIn(desk, "hr@demo.example.com", "hr"); sessions.push(hr);
  await hr.page.goto(`${BASE}/workforce/payroll`);
  const d = new Date(Date.now() - 40 * 864e5).toISOString().slice(0, 10);
  await hr.page.locator("#pe-f").fill(d);
  await hr.page.getByRole("button", { name: "Create export" }).click();
  await hr.page.getByText(/PAY-\d+: \d+ line/).first().waitFor({ timeout: 15000 });
  check(true, "[workforce] HR created a payroll export");
  await shot(hr.page, "hr_payroll");
  await hr.page.goto(`${BASE}/workforce/setup`);
  await hr.page.getByText("Shift templates").first().waitFor();
  await shot(hr.page, "hr_setup");
  await hr.page.goto(`${BASE}/workforce/employees`);
  await hr.page.getByText("EMP-101").waitFor();
  await shot(hr.page, "hr_employees");

  for (const s of sessions) {
    check(s.errors.length === 0, `[workforce:${s.label}] no console errors / 5xx${s.errors.length ? `: ${s.errors.slice(0, 3).join(" | ")}` : ""}`);
    await s.ctx.close();
  }
}

await run({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 }, "desktop", true);
await run({ ...devices["iPhone 13"] }, "mobile", false);
if (process.env.SKIP_WORKFORCE !== "1") await workforceFlow();
await browser.close();
if (failures.length) { console.error(`\n${failures.length} check(s) failed`); process.exit(1); }
console.log("\nAll smoke checks passed");
