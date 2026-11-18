import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, MessageSquarePlus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Badge, Button, Skeleton } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AgentInfo, Conversation, Page } from "@/types/api";
import { ChatPanel, SectionTitle } from "./ChatPanel";

export function CommandCenterPage() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [showList, setShowList] = useState(false);
  const convs = useQuery({ queryKey: ["conversations"], queryFn: () => api.get<Page<Conversation>>("/ai/conversations", { page_size: 50 }) });
  const agents = useQuery({ queryKey: ["ai-agents"], queryFn: () => api.get<{ agents: AgentInfo[]; not_connected: string[]; engine: string; model: string | null }>("/ai/agents") });

  const archive = async (id: string) => {
    await api.del(`/ai/conversations/${id}`);
    qc.invalidateQueries({ queryKey: ["conversations"] });
    if (id === conversationId) navigate("/ai");
  };

  return (
    <div className="flex h-[calc(100vh-3.5rem)]">
      <aside className={cn("w-72 shrink-0 flex-col border-r border-line bg-surface md:flex", showList ? "fixed inset-y-14 left-0 z-20 flex" : "hidden")}>
        <div className="p-3">
          <Button variant="primary" className="w-full" onClick={() => { navigate("/ai"); setShowList(false); }}><MessageSquarePlus /> New conversation</Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto pb-3">
          <SectionTitle>Conversations</SectionTitle>
          {convs.isLoading && <div className="space-y-2 px-3">{[1, 2, 3].map((i) => <Skeleton key={i} className="h-9" />)}</div>}
          {convs.data?.items.length === 0 && <p className="px-3 py-2 text-xs text-muted">No conversations yet.</p>}
          <ul className="space-y-0.5 px-2">
            {convs.data?.items.map((c) => (
              <li key={c.id} className="group relative">
                <button type="button" onClick={() => { navigate(`/ai/${c.id}`); setShowList(false); }}
                  className={cn("w-full rounded-lg px-2.5 py-2 pr-8 text-left hover:bg-surface-2", c.id === conversationId && "bg-surface-2")}>
                  <div className="truncate text-[13px]">{c.title}</div>
                  <div className="text-[11px] text-muted">{relative(c.updated_at)}</div>
                </button>
                <button type="button" onClick={() => archive(c.id)} aria-label={`Archive conversation ${c.title}`}
                  className="absolute right-1.5 top-2 hidden rounded p-1 text-muted hover:bg-surface hover:text-bad group-hover:block focus:block">
                  <Trash2 className="size-3.5" />
                </button>
              </li>
            ))}
          </ul>
          <SectionTitle>Connected agents</SectionTitle>
          <div className="space-y-2 px-3">
            {agents.data?.agents.map((a) => (
              <div key={a.id} className="rounded-lg border border-line p-2.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-1.5 text-[13px] font-medium"><Bot className="size-3.5 text-accent" />{a.name}</span>
                  <Badge tone="ok">{a.status}</Badge>
                </div>
                <p className="mt-1 text-[11.5px] leading-snug text-muted">{a.tools.length} tools available to you · v{a.version}</p>
              </div>
            ))}
            {agents.data && (
              <div className="rounded-lg border border-dashed border-line p-2.5 text-[11.5px] text-muted">
                <div className="mb-1 font-medium text-fg-2">Not connected yet</div>
                {agents.data.not_connected.join(" · ")}
              </div>
            )}
            {agents.data && (
              <p className="text-[11px] text-muted">Engine: {agents.data.engine === "claude" ? `Claude (${agents.data.model})` : "Offline (no API key configured)"}</p>
            )}
          </div>
        </div>
      </aside>
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-line px-4 py-2 md:hidden">
          <Button size="sm" variant="ghost" onClick={() => setShowList((s) => !s)}>{showList ? "Close" : "Conversations"}</Button>
        </div>
        <ChatPanel key={conversationId ?? "new"} conversationId={conversationId} onConversationChange={(id) => navigate(`/ai/${id}`, { replace: true })} />
      </section>
    </div>
  );
}
