/** Formatting helpers. Quantities and money arrive from the API as decimal strings. */
let locale = "en-IN";
let currency = "INR";
let timeZone: string | undefined;

export function configureFormatting(opts: { locale?: string; currency?: string; timeZone?: string }): void {
  if (opts.locale) locale = opts.locale;
  if (opts.currency) currency = opts.currency;
  if (opts.timeZone) timeZone = opts.timeZone;
}

export function currencyCode(): string {
  return currency;
}

export function qty(v: string | number | null | undefined, maxFraction = 3): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return String(v);
  return new Intl.NumberFormat(locale, { maximumFractionDigits: maxFraction }).format(n);
}

export function money(v: string | number | null | undefined, opts: { compact?: boolean; symbol?: boolean } = {}): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return String(v);
  return new Intl.NumberFormat(locale, {
    style: opts.symbol === false ? "decimal" : "currency",
    currency,
    notation: opts.compact ? "compact" : "standard",
    maximumFractionDigits: opts.compact ? 1 : 2,
    minimumFractionDigits: opts.compact ? 0 : 2,
  }).format(n);
}

export function dateTime(v: string | null | undefined): string {
  if (!v) return "—";
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short", timeZone }).format(new Date(v));
}

export function date(v: string | null | undefined): string {
  if (!v) return "—";
  const d = v.length === 10 ? new Date(`${v}T00:00:00`) : new Date(v);
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeZone: v.length === 10 ? undefined : timeZone }).format(d);
}

export function relative(v: string | null | undefined): string {
  if (!v) return "—";
  const diff = (new Date(v).getTime() - Date.now()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  const abs = Math.abs(diff);
  if (abs < 60) return rtf.format(Math.round(diff), "second");
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  return rtf.format(Math.round(diff / 86400), "day");
}

export function label(v: string | null | undefined): string {
  if (!v) return "—";
  return v.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export function today(): string {
  return new Date().toISOString().slice(0, 10);
}
