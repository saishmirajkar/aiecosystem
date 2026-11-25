import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { LoginPage } from "@/features/auth/AuthPages";
import { ME, jsonResponse, mockFetch, renderApp } from "./utils";

describe("LoginPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("signs in and sends the CSRF header", async () => {
    const calls = mockFetch({
      "GET /auth/session": () => ({ authenticated: false, user: null }),
      "POST /auth/login": () => ({ user: ME, token: "t", expires_at: "2026-11-19T00:00:00Z" }),
    });
    renderApp(<Routes><Route path="/login" element={<LoginPage />} /><Route path="/" element={<p>Home</p>} /></Routes>, { route: "/login" });
    await userEvent.type(screen.getByLabelText("Email or employee code"), "admin@demo.example.com");
    await userEvent.type(screen.getByLabelText("Password"), "Admin-Passw0rd!");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(screen.getByText("Home")).toBeInTheDocument());
    const login = calls.find((c) => c.url.includes("/auth/login"))!;
    expect(login.headers["X-Requested-With"]).toBe("above-web");
    expect(login.body).toEqual({ email: "admin@demo.example.com", password: "Admin-Passw0rd!" });
  });

  it("shows server errors", async () => {
    mockFetch({
      "GET /auth/session": () => ({ authenticated: false, user: null }),
      "POST /auth/login": () => jsonResponse({ error: { code: "invalid_credentials", message: "Invalid email or password" } }, 401),
    });
    renderApp(<LoginPage />, { route: "/login" });
    await userEvent.type(screen.getByLabelText("Email or employee code"), "x@demo.example.com");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
  });
});
