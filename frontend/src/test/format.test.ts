import { ApiError } from "@/lib/api";
import { configureFormatting, label, money, qty } from "@/lib/format";

describe("formatting", () => {
  beforeAll(() => configureFormatting({ locale: "en-IN", currency: "INR" }));
  it("formats decimal strings without float noise", () => {
    expect(qty("1200.0000")).toBe("1,200");
    expect(qty("96.5000")).toBe("96.5");
    expect(qty(null)).toBe("—");
  });
  it("formats money in the company currency", () => {
    expect(money("123000.0000")).toContain("1,23,000.00");
    expect(money("2180000", { compact: true })).toMatch(/21\.8L|2\.2M/);
  });
  it("humanises enum labels", () => expect(label("pending_approval")).toBe("Pending approval"));
});

describe("ApiError", () => {
  it("maps validation errors to fields", () => {
    const e = new ApiError(422, { code: "validation_error", message: "Request validation failed",
      details: { errors: [{ loc: ["body", "password"], msg: "Value error, Password must be at least 10 characters" }] } });
    expect(e.fieldErrors()).toEqual({ password: "Password must be at least 10 characters" });
  });
});
