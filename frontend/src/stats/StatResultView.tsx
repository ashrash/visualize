import type { StatResult } from "../api/types";

const isPlainObject = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

const format = (v: unknown): string => {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return v.toLocaleString(undefined, { maximumFractionDigits: 4 });
  if (typeof v === "boolean") return v ? "true" : "false";
  return String(v);
};

/**
 * Generic renderer for a StatResult's `values` bag (TODO(stats): §5 tests). The shape varies per
 * test per the conventions in backend/src/viz/stats/results.py (scalars, curves, groups,
 * intervals), so this renders a key/value table for scalar entries, a mini table for an array of
 * scalars, and falls back to JSON for anything nested (curves, group lists). A dedicated renderer
 * per test kind can replace this once the real endpoints and shapes land.
 */
export function StatResultView({ result }: { result: StatResult }) {
  const entries = Object.entries(result.values);
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-muted">
        {result.method} · n = {result.n.toLocaleString()}
        {result.sampled ? " · sampled" : ""}
      </p>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
        {entries.map(([key, value]) => (
          <div key={key} className="contents">
            <dt className="text-muted">{key}</dt>
            <dd className="min-w-0 tabular-nums">
              {Array.isArray(value) &&
              value.every((v) => !isPlainObject(v) && !Array.isArray(v)) ? (
                value.map(format).join(", ")
              ) : isPlainObject(value) || Array.isArray(value) ? (
                <pre className="overflow-x-auto whitespace-pre-wrap break-all text-xs">
                  {JSON.stringify(value, null, 2)}
                </pre>
              ) : (
                format(value)
              )}
            </dd>
          </div>
        ))}
      </dl>
      {(result.warnings ?? []).length > 0 ? (
        <ul className="list-disc pl-4 text-xs text-muted">
          {(result.warnings ?? []).map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
