import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ClaimButton } from "@/features/workforce/EmployeePages";
import { addDays, fromLocalInput, hrs, mondayOf, toLocalInput } from "@/features/workforce/shared";
import type { Opportunity } from "@/types/workforce";
import { jsonResponse, mockFetch, renderApp } from "./utils";

const OPP: Opportunity = {
  id: "o1", number: "OT-000001", title: "Morning", status: "open", source: "leave", shift_id: "s1", shift_name: "Morning", date: "2026-11-27",
  department_id: "d1", department_name: "Production", team_id: null, team_name: null, work_location_name: null, start_at: "2026-11-27T00:30:00Z",
  end_at: "2026-11-27T08:30:00Z", start: "06:00", end: "14:00", total_minutes: 480, break_minutes: 30, payable_minutes: 450, positions_total: 1,
  positions_filled: 0, positions_left: 1, required_skills: [], claim_deadline: "2026-11-26T20:30:00Z", claims_open_at: null, round: 1,
  created_at: "2026-11-24T00:00:00Z", notes: null, eligible: true, claimed_by_me: false,
};

describe("workforce time helpers", () => {
  it("round-trips company-local times across midnight and time zones", () => {
    const iso = fromLocalInput("2026-11-27T22:15", "Asia/Kolkata")!;
    expect(iso).toBe("2026-11-27T16:45:00.000Z");
    expect(toLocalInput(iso, "Asia/Kolkata")).toBe("2026-11-27T22:15");
  });
  it("computes weeks and hours", () => {
    expect(mondayOf("2026-11-29")).toBe("2026-11-23");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(hrs(90)).toBe("1.5 h");
    expect(hrs(480)).toBe("8 h");
  });
});

describe("ClaimButton", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is disabled for an ineligible employee", () => {
    mockFetch({ "GET /auth/session": () => ({ authenticated: false, user: null }) });
    renderApp(<ClaimButton o={{ ...OPP, eligible: false, ineligible_reasons: ["Already working Day on 27 Nov"] }} />);
    expect(screen.getByRole("button", { name: "Not eligible" })).toBeDisabled();
  });

  it("claims with an idempotency key and shows the server's answer when someone else won", async () => {
    const calls = mockFetch({
      "GET /auth/session": () => ({ authenticated: false, user: null }),
      "POST /overtime/opportunities/o1/claim": () => jsonResponse({ error: { code: "opportunity_filled", message: "This overtime opportunity has already been claimed by another employee." } }, 409),
    });
    renderApp(<ClaimButton o={OPP} />);
    await userEvent.click(screen.getByRole("button", { name: "Claim shift" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/claim"))).toBe(true));
    const claim = calls.find((c) => c.url.includes("/claim"))!;
    expect(claim.headers["Idempotency-Key"]).toBeTruthy();
    expect(claim.headers["X-Requested-With"]).toBe("above-web");
  });

  it("shows confirmation once the employee holds the shift", () => {
    mockFetch({ "GET /auth/session": () => ({ authenticated: false, user: null }) });
    renderApp(<ClaimButton o={{ ...OPP, claimed_by_me: true }} />);
    expect(screen.getByText("You’re confirmed")).toBeInTheDocument();
  });
});
