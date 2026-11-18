import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatPanel } from "@/features/ai-command-center/ChatPanel";
import { ME, mockFetch, renderApp } from "./utils";

const approval = { id: "a1", action_type: "purchasing.create_requisition", params: {}, summary: "Create purchase requisition: 300 KG STL-PL-12",
  preview: null, status: "pending", required_permission: "purchasing.requisition.create", requested_by: "u1", requested_via: "ai", agent_id: "inventory",
  conversation_id: "c1", decided_by: null, decided_at: null, decision_note: null, result: null, error: null, expires_at: "2026-11-19T10:00:00Z", created_at: "2026-11-18T10:00:00Z" };

function conversation(status: string) {
  return {
    id: "c1", title: "Create a purchase request",
    messages: [
      { id: "m1", conversation_id: "c1", role: "user", content: "Create a purchase request for 300 kg of STL-PL-12", payload: null, status: "ok", created_at: "2026-11-18T10:00:00Z" },
      { id: "m2", conversation_id: "c1", role: "assistant", status: "ok", created_at: "2026-11-18T10:00:01Z",
        content: "I've prepared a **draft** for your approval.",
        payload: { text: "", engine: "offline", status: "ok", notices: [], tool_calls: [{ tool: "create_purchase_requisition_draft", status: "success", duration_ms: 12, error: null }],
          routing: { agents: [{ id: "inventory", name: "Inventory Agent" }], reason: "", unconnected: [] },
          approvals: [{ ...approval, status, result: status === "executed" ? { number: "PR-000002", status: "pending_approval", link: "/purchasing/requisitions/x" } : null }],
          cards: [{ type: "approval", title: "Proposed purchase requisition", provenance: "draft", approval_id: "a1", columns: [{ key: "sku", label: "SKU" }],
            rows: [{ sku: "STL-PL-12" }], sources: [], as_of: "2026-11-18T10:00:01Z", note: "Approving creates the requisition." }] } },
    ],
  };
}

describe("ChatPanel", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders draft cards with provenance and approves the bound action", async () => {
    let executed = false;
    const calls = mockFetch({
      "GET /auth/session": () => ({ authenticated: true, user: ME }),
      "GET /ai/suggestions": () => [],
      "GET /ai/conversations/c1": () => conversation(executed ? "executed" : "pending"),
      "POST /approvals/a1/approve": () => { executed = true; return { ...approval, status: "executed", result: { number: "PR-000002" } }; },
    });
    renderApp(<ChatPanel conversationId="c1" />);
    const card = (await screen.findByText("Proposed purchase requisition")).closest("div.overflow-hidden") as HTMLElement;
    expect(within(card).getByText("Draft action")).toBeInTheDocument();
    expect(screen.getByText("draft").tagName).toBe("STRONG"); // safe markdown, no HTML injection
    await userEvent.click(within(card).getByRole("button", { name: "Approve & execute" }));
    await waitFor(() => expect(screen.getByText(/Approved and executed/)).toBeInTheDocument());
    expect(calls.some((c) => c.method === "POST" && c.url.includes("/approvals/a1/approve"))).toBe(true);
  });

  it("sends a message and shows suggestions on a new conversation", async () => {
    const calls = mockFetch({
      "GET /auth/session": () => ({ authenticated: true, user: ME }),
      "GET /ai/suggestions": () => ["Which items are running low?"],
      "POST /ai/chat": () => ({ conversation: { id: "c1", title: "x" }, message: {} }),
      "GET /ai/conversations/c1": () => conversation("pending"),
    });
    renderApp(<ChatPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Which items are running low?" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/ai/chat"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/ai/chat"))!.body).toEqual({ message: "Which items are running low?", conversation_id: null });
  });
});
