import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as DropdownPrimitive from "@radix-ui/react-dropdown-menu";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { X } from "lucide-react";
import { useState, type ComponentPropsWithoutRef, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Button, Field, Textarea } from "./primitives";

export function Dialog({ open, onOpenChange, title, description, children, footer, size = "md" }: {
  open: boolean; onOpenChange: (open: boolean) => void; title: string; description?: string; children: ReactNode; footer?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
}) {
  const width = { sm: "max-w-md", md: "max-w-xl", lg: "max-w-3xl", xl: "max-w-5xl" }[size];
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px]" />
        <DialogPrimitive.Content
          className={cn("fixed left-1/2 top-1/2 z-50 flex max-h-[90vh] w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-line bg-surface shadow-2xl", width)}>
          <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
            <div>
              <DialogPrimitive.Title className="text-base font-semibold tracking-tight">{title}</DialogPrimitive.Title>
              {description ? <DialogPrimitive.Description className="mt-1 text-sm text-muted">{description}</DialogPrimitive.Description>
                : <DialogPrimitive.Description className="sr-only">{title}</DialogPrimitive.Description>}
            </div>
            <DialogPrimitive.Close asChild>
              <Button variant="ghost" size="icon" aria-label="Close"><X /></Button>
            </DialogPrimitive.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
          {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/** Confirmation dialog, optionally asking for a reason (cancel/reject/close actions). */
export function ConfirmDialog({ open, onOpenChange, title, description, confirmLabel = "Confirm", tone = "primary", requireReason, reasonLabel = "Reason", onConfirm, loading, children }: {
  open: boolean; onOpenChange: (o: boolean) => void; title: string; description?: string; confirmLabel?: string;
  tone?: "primary" | "danger" | "accent"; requireReason?: boolean; reasonLabel?: string; onConfirm: (reason: string) => void; loading?: boolean;
  children?: ReactNode;
}) {
  const [reason, setReason] = useState("");
  const invalid = requireReason && reason.trim().length < 3;
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) setReason(""); onOpenChange(o); }} title={title} description={description} size="sm"
      footer={<>
        <Button onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant={tone} loading={loading} disabled={invalid} onClick={() => onConfirm(reason.trim())}>{confirmLabel}</Button>
      </>}>
      {children}
      {requireReason && (
        <Field label={reasonLabel} htmlFor="confirm-reason" hint="At least 3 characters; stored in the audit trail.">
          <Textarea id="confirm-reason" value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
        </Field>
      )}
    </Dialog>
  );
}

export const DropdownMenu = DropdownPrimitive.Root;
export const DropdownTrigger = DropdownPrimitive.Trigger;
export function DropdownContent({ className, ...props }: ComponentPropsWithoutRef<typeof DropdownPrimitive.Content>) {
  return (
    <DropdownPrimitive.Portal>
      <DropdownPrimitive.Content sideOffset={6} align="end"
        className={cn("z-50 min-w-48 rounded-lg border border-line bg-surface p-1 shadow-xl", className)} {...props} />
    </DropdownPrimitive.Portal>
  );
}
export function DropdownItem({ className, ...props }: ComponentPropsWithoutRef<typeof DropdownPrimitive.Item>) {
  return <DropdownPrimitive.Item className={cn("flex cursor-pointer select-none items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-fg-2 outline-none data-[highlighted]:bg-surface-2 data-[highlighted]:text-fg [&_svg]:size-4", className)} {...props} />;
}
export const DropdownSeparator = () => <DropdownPrimitive.Separator className="my-1 h-px bg-line" />;
export function DropdownLabel({ children }: { children: ReactNode }) {
  return <DropdownPrimitive.Label className="px-2.5 py-1.5 text-xs text-muted">{children}</DropdownPrimitive.Label>;
}

export const Tabs = TabsPrimitive.Root;
export function TabsList({ className, ...props }: ComponentPropsWithoutRef<typeof TabsPrimitive.List>) {
  return <TabsPrimitive.List className={cn("flex gap-1 border-b border-line", className)} {...props} />;
}
export function TabsTrigger({ className, ...props }: ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>) {
  return <TabsPrimitive.Trigger className={cn("-mb-px border-b-2 border-transparent px-3 py-2 text-sm text-muted transition-colors hover:text-fg data-[state=active]:border-accent data-[state=active]:text-fg", className)} {...props} />;
}
export const TabsContent = TabsPrimitive.Content;
