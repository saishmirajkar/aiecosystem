import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { Button, Checkbox, Field, Input, Select } from "@/components/ui/primitives";
import { useAuth } from "@/hooks/auth";
import { api, ApiError, errorMessage } from "@/lib/api";

function AuthLayout({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <div className="grid min-h-full lg:grid-cols-[1.05fr_1fr]">
      <div className="relative hidden overflow-hidden bg-[#0f0f0e] p-12 text-[#ece8df] lg:flex lg:flex-col lg:justify-between">
        <div className="flex items-center gap-2.5">
          <svg viewBox="0 0 32 32" className="size-8"><rect width="32" height="32" rx="7" fill="#ece8df" /><path d="M8 22 16 9l8 13" fill="none" stroke="#8a7148" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" /><path d="M11.5 22h9" stroke="#0f0f0e" strokeWidth="2.6" strokeLinecap="round" /></svg>
          <span className="text-sm font-semibold tracking-[0.2em]">ABOVE STUDIO</span>
        </div>
        <div className="max-w-md">
          <p className="text-[11px] uppercase tracking-[0.24em] text-[#c9b48a]">Industrial AI ecosystem</p>
          <h2 className="mt-4 text-4xl font-semibold leading-[1.1] tracking-tight">Your entire business.<br />Within reach.</h2>
          <p className="mt-4 text-[15px] leading-relaxed text-[#bdb8ad]">Every department. One intelligence. An ERP foundation and inventory system with its own intelligence layer — grounded in your real operational records.</p>
        </div>
        <div className="grid grid-cols-3 gap-6 border-t border-[#2c2b28] pt-6 text-xs text-[#8a867d]">
          <div><div className="num text-lg text-[#ece8df]">Ledger</div>Immutable stock history</div>
          <div><div className="num text-lg text-[#ece8df]">RBAC</div>Enforced on every call</div>
          <div><div className="num text-lg text-[#ece8df]">Approvals</div>For every AI action</div>
        </div>
      </div>
      <div className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          <p className="mt-1.5 text-sm text-muted">{subtitle}</p>
          <div className="mt-8">{children}</div>
        </div>
      </div>
    </div>
  );
}

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <AuthLayout title="Sign in" subtitle="Use your company account to continue.">
      <form onSubmit={submit} className="space-y-4" noValidate>
        <Field label="Email or employee code" htmlFor="email"><Input id="email" type="text" autoComplete="username" autoCapitalize="none" spellCheck={false} value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus /></Field>
        <Field label="Password" htmlFor="password"><Input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
        {error && <p className="rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad" role="alert">{error}</p>}
        <Button type="submit" variant="primary" className="w-full" loading={busy} disabled={!email || !password}>Sign in</Button>
        <div className="text-center"><Link to="/reset-password" className="text-sm text-muted hover:text-fg">Forgot password?</Link></div>
      </form>
    </AuthLayout>
  );
}

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const token = params.get("token");
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (token) {
        await api.post("/auth/password-reset/confirm", { token, new_password: password });
        toast.success("Password updated — please sign in");
        navigate("/login");
      } else {
        await api.post("/auth/password-reset/request", { email });
        setSent(true);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.fieldErrors().new_password ?? err.message : errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <AuthLayout title={token ? "Choose a new password" : "Reset password"} subtitle={token ? "At least 10 characters, mixing letters and numbers or symbols." : "We’ll send a reset link if the account exists."}>
      {sent ? <p className="rounded-lg bg-ok-soft px-3 py-3 text-sm text-ok">If an account exists for {email}, a reset link has been sent. Ask your administrator if email is not configured.</p> : (
        <form onSubmit={submit} className="space-y-4">
          {token ? <Field label="New password" htmlFor="np"><Input id="np" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
            : <Field label="Email" htmlFor="em"><Input id="em" type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></Field>}
          {error && <p className="text-sm text-bad" role="alert">{error}</p>}
          <Button type="submit" variant="primary" className="w-full" loading={busy}>{token ? "Update password" : "Send reset link"}</Button>
        </form>
      )}
      <div className="mt-4 text-center"><Link to="/login" className="text-sm text-muted hover:text-fg">Back to sign in</Link></div>
    </AuthLayout>
  );
}

const TIMEZONES = ["Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "Europe/London", "Europe/Berlin", "America/New_York", "America/Chicago", "UTC"];
const CURRENCIES = ["INR", "USD", "EUR", "GBP", "AED", "SGD"];

export function SetupPage({ onDone }: { onDone: () => void }) {
  const [form, setForm] = useState({ company: "", industry: "Mechanical engineering", currency: "INR", timezone: "Asia/Kolkata",
    adminName: "", adminEmail: "", password: "", demo: false });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErrors({});
    try {
      await api.post("/setup/initialize", {
        company: { name: form.company, industry: form.industry, currency_code: form.currency, timezone: form.timezone, number_format: form.currency === "INR" ? "en-IN" : "en-US" },
        admin: { full_name: form.adminName, email: form.adminEmail, password: form.password },
        load_demo_data: form.demo,
      });
      toast.success("Installation initialised — sign in with your administrator account");
      onDone();
    } catch (err) {
      if (err instanceof ApiError) {
        const fe = err.fieldErrors();
        setErrors(Object.keys(fe).length ? fe : { form: err.message });
      } else setErrors({ form: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <AuthLayout title="Set up this installation" subtitle="This dedicated deployment has no company yet. Configure it once to begin.">
      <form onSubmit={submit} className="space-y-4">
        <Field label="Company name" htmlFor="co" error={errors.name} required><Input id="co" value={form.company} onChange={set("company")} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Currency" htmlFor="cur"><Select id="cur" value={form.currency} onChange={set("currency")}>{CURRENCIES.map((c) => <option key={c}>{c}</option>)}</Select></Field>
          <Field label="Time zone" htmlFor="tz"><Select id="tz" value={form.timezone} onChange={set("timezone")}>{TIMEZONES.map((t) => <option key={t}>{t}</option>)}</Select></Field>
        </div>
        <Field label="Industry" htmlFor="ind"><Input id="ind" value={form.industry} onChange={set("industry")} /></Field>
        <div className="border-t border-line pt-4 text-xs font-medium uppercase tracking-wider text-muted">Super administrator</div>
        <Field label="Full name" htmlFor="an" error={errors.full_name} required><Input id="an" value={form.adminName} onChange={set("adminName")} /></Field>
        <Field label="Email" htmlFor="ae" error={errors.email} required><Input id="ae" type="email" value={form.adminEmail} onChange={set("adminEmail")} /></Field>
        <Field label="Password" htmlFor="ap" error={errors.password} hint="At least 10 characters with letters and numbers/symbols." required>
          <Input id="ap" type="password" autoComplete="new-password" value={form.password} onChange={set("password")} />
        </Field>
        <Checkbox label="Load synthetic demo data (evaluation environments only)" checked={form.demo} onChange={(e) => setForm((f) => ({ ...f, demo: e.target.checked }))} />
        {errors.form && <p className="rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad" role="alert">{errors.form}</p>}
        <Button type="submit" variant="primary" className="w-full" loading={busy} disabled={!form.company || !form.adminEmail || !form.password || !form.adminName}>Initialise installation</Button>
      </form>
    </AuthLayout>
  );
}
