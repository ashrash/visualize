/**
 * Number, currency, percent and date formatting for charts, on top of `Intl`.
 *
 * A format is a short string so it can live in a ChartSpec: `kind[:arg[:arg]]`.
 *   auto            adaptive number (up to 6 significant decimals)
 *   number[:d]      fixed decimals, thousands separators          number:2 -> 1,234.50
 *   compact[:d]     1.2K / 3.4M                                   compact:1
 *   percent[:d]     value is a fraction                           percent:1 -> 12.5%
 *   percent100[:d]  value is already 0-100                        percent100:0 -> 12%
 *   currency[:CODE[:d]]                                           currency:USD:0 -> $1,235
 *   date | datetime | time | month | year | quarter
 * Anything unparseable falls back to `auto`.
 */

export type ValueFormatter = (value: unknown) => string;

export interface FormatPreset {
  value: string;
  label: string;
  group: "number" | "date";
}

export const FORMAT_PRESETS: FormatPreset[] = [
  { value: "auto", label: "Automatic", group: "number" },
  { value: "number:0", label: "1,235", group: "number" },
  { value: "number:2", label: "1,234.50", group: "number" },
  { value: "compact:1", label: "1.2K / 3.4M", group: "number" },
  { value: "percent:0", label: "12% (fraction)", group: "number" },
  { value: "percent:1", label: "12.5% (fraction)", group: "number" },
  { value: "percent100:0", label: "12% (already 0-100)", group: "number" },
  { value: "currency:USD:0", label: "$1,235", group: "number" },
  { value: "currency:USD:2", label: "$1,234.50", group: "number" },
  { value: "currency:EUR:2", label: "EUR 1,234.50", group: "number" },
  { value: "date", label: "2026-10-30", group: "date" },
  { value: "datetime", label: "2026-10-30 14:05", group: "date" },
  { value: "month", label: "Oct 2026", group: "date" },
  { value: "quarter", label: "2026 Q4", group: "date" },
  { value: "year", label: "2026", group: "date" },
  { value: "time", label: "14:05", group: "date" },
];

const cache = new Map<string, Intl.NumberFormat>();

function numberFormat(locale: string | undefined, options: Intl.NumberFormatOptions) {
  const key = `${locale ?? ""}|${JSON.stringify(options)}`;
  let format = cache.get(key);
  if (!format) {
    format = new Intl.NumberFormat(locale, options);
    cache.set(key, format);
  }
  return format;
}

function digits(arg: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(arg ?? "", 10);
  return Number.isFinite(parsed) && parsed >= 0 && parsed <= 20 ? parsed : fallback;
}

const pad = (n: number, width = 2) => String(n).padStart(width, "0");

/** Dates are epoch ms (as `toColumns` delivers them) or ISO strings; shown in UTC like the grid. */
export function toDate(value: unknown): Date | null {
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value === "number" || typeof value === "string") {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }
  return null;
}

function formatDate(kind: string, date: Date, locale?: string): string {
  const y = date.getUTCFullYear();
  const m = date.getUTCMonth();
  switch (kind) {
    case "year":
      return String(y);
    case "quarter":
      return `${y} Q${Math.floor(m / 3) + 1}`;
    case "month":
      return new Intl.DateTimeFormat(locale, {
        month: "short",
        year: "numeric",
        timeZone: "UTC",
      }).format(date);
    case "time":
      return `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
    case "datetime":
      return `${y}-${pad(m + 1)}-${pad(date.getUTCDate())} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
    default:
      return `${y}-${pad(m + 1)}-${pad(date.getUTCDate())}`;
  }
}

/** Compact formatting that adapts: midnight timestamps drop the time of day. */
export function formatDateAuto(value: unknown): string {
  const date = toDate(value);
  if (!date) return "";
  const midnight = date.getTime() % 86_400_000 === 0;
  return formatDate(midnight ? "date" : "datetime", date);
}

const DATE_KINDS = new Set(["date", "datetime", "time", "month", "year", "quarter"]);

export function isDateFormat(format: string | null | undefined): boolean {
  return DATE_KINDS.has((format ?? "").split(":")[0] ?? "");
}

/** Build a formatter for a format string. `null` and unknown strings give the `auto` formatter. */
export function makeFormatter(
  format: string | null | undefined,
  locale?: string,
): ValueFormatter {
  const [kind = "auto", a, b] = (format ?? "auto").split(":");

  if (DATE_KINDS.has(kind)) {
    return (value) => {
      const date = toDate(value);
      return date ? formatDate(kind, date, locale) : "";
    };
  }

  let nf: Intl.NumberFormat;
  let scale = 1;
  switch (kind) {
    case "number":
      nf = numberFormat(locale, {
        minimumFractionDigits: digits(a, 0),
        maximumFractionDigits: digits(a, 0),
      });
      break;
    case "compact":
      nf = numberFormat(locale, {
        notation: "compact",
        maximumFractionDigits: digits(a, 1),
      });
      break;
    case "percent":
      nf = numberFormat(locale, {
        style: "percent",
        minimumFractionDigits: digits(a, 0),
        maximumFractionDigits: digits(a, 0),
      });
      break;
    case "percent100":
      scale = 0.01;
      nf = numberFormat(locale, {
        style: "percent",
        minimumFractionDigits: digits(a, 0),
        maximumFractionDigits: digits(a, 0),
      });
      break;
    case "currency":
      nf = numberFormat(locale, {
        style: "currency",
        currency: /^[A-Za-z]{3}$/.test(a ?? "") ? (a as string).toUpperCase() : "USD",
        minimumFractionDigits: digits(b, 2),
        maximumFractionDigits: digits(b, 2),
      });
      break;
    default:
      nf = numberFormat(locale, { maximumFractionDigits: 6 });
  }

  return (value) => {
    if (value === null || value === undefined || value === "") return "";
    const n = typeof value === "number" ? value : Number(value);
    if (!Number.isFinite(n)) return String(value);
    return nf.format(n * scale);
  };
}
