import { cva, type VariantProps } from "class-variance-authority";
import { Loader2 } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes, type HTMLAttributes, type InputHTMLAttributes, type LabelHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-fg text-bg hover:bg-fg/90",
        accent: "bg-accent text-accent-fg hover:bg-accent/90",
        secondary: "border border-line bg-surface text-fg hover:bg-surface-2",
        ghost: "text-fg-2 hover:bg-surface-2 hover:text-fg",
        danger: "bg-bad text-white hover:bg-bad/90",
        link: "text-accent underline-offset-4 hover:underline px-0 h-auto",
      },
      size: { sm: "h-8 px-3 text-[13px]", md: "h-9 px-3.5", lg: "h-10 px-5", icon: "h-9 w-9" },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, loading, children, disabled, type = "button", ...props }, ref) => (
  <button ref={ref} type={type} className={cn(buttonVariants({ variant, size }), className)} disabled={disabled || loading} {...props}>
    {loading && <Loader2 className="animate-spin" aria-hidden />}
    {children}
  </button>
));
Button.displayName = "Button";

const fieldBase =
  "w-full rounded-lg border border-line bg-surface px-3 text-sm text-fg placeholder:text-muted transition-colors hover:border-line-strong focus:border-accent focus:outline-none disabled:opacity-60 aria-[invalid=true]:border-bad";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(({ className, ...props }, ref) => (
  <input ref={ref} className={cn(fieldBase, "h-9", className)} {...props} />
));
Input.displayName = "Input";

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(({ className, children, ...props }, ref) => (
  <select ref={ref} className={cn(fieldBase, "h-9 pr-8 appearance-none bg-[length:16px] bg-[right_0.6rem_center] bg-no-repeat", className)}
    style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%237b776e' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")" }}
    {...props}>
    {children}
  </select>
));
Select.displayName = "Select";

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, ...props }, ref) => (
  <textarea ref={ref} className={cn(fieldBase, "min-h-[76px] py-2", className)} {...props} />
));
Textarea.displayName = "Textarea";

export function Label({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("text-[13px] font-medium text-fg-2", className)} {...props} />;
}

export function Field({ label, htmlFor, error, hint, children, className, required }: {
  label: string; htmlFor?: string; error?: string; hint?: string; children: ReactNode; className?: string; required?: boolean;
}) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label htmlFor={htmlFor}>{label}{required && <span className="text-bad"> *</span>}</Label>
      {children}
      {error ? <p className="text-xs text-bad" role="alert">{error}</p> : hint ? <p className="text-xs text-muted">{hint}</p> : null}
    </div>
  );
}

export function Checkbox({ label, className, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  return (
    <label className={cn("inline-flex cursor-pointer items-center gap-2 text-sm text-fg-2", className)}>
      <input type="checkbox" className="size-4 rounded border-line accent-[var(--accent)]" {...props} />
      {label}
    </label>
  );
}

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-xl border border-line bg-surface shadow-[var(--shadow)]", className)} {...props} />;
}

export function CardHeader({ title, description, actions, className }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-start justify-between gap-3 border-b border-line px-5 py-3.5", className)}>
      <div className="min-w-0">
        <h3 className="text-sm font-semibold tracking-tight">{title}</h3>
        {description && <p className="mt-0.5 text-xs text-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

const badgeVariants = cva("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide whitespace-nowrap", {
  variants: {
    tone: {
      neutral: "bg-surface-2 text-fg-2",
      ok: "bg-ok-soft text-ok",
      warn: "bg-warn-soft text-warn",
      bad: "bg-bad-soft text-bad",
      info: "bg-info-soft text-info",
      accent: "bg-accent-soft text-accent",
    },
  },
  defaultVariants: { tone: "neutral" },
});
export type Tone = NonNullable<VariantProps<typeof badgeVariants>["tone"]>;

export function Badge({ tone, className, ...props }: HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-md bg-surface-2", className)} aria-hidden />;
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("size-4 animate-spin text-muted", className)} aria-label="Loading" />;
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-line bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-muted">{children}</kbd>;
}
