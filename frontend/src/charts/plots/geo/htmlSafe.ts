/**
 * Escape a value for use inside a tooltip formatter's returned HTML string.
 *
 * ECharts 6.1 inserts a tooltip formatter's return value straight into `innerHTML` with no
 * escaping of its own (https://echarts.apache.org/en/option.html#tooltip.formatter). Every
 * value interpolated into a geo tooltip can come from user data — a region name that failed
 * to match the map, a color-group key, or a measure label taken from a CSV header or an
 * encoding's `label` — so every one of them must be escaped here before it reaches the
 * template string. CLAUDE.md's import-from-path and the upcoming share/embed views (Wave 3)
 * make this stored XSS otherwise: a header or cell like `<img src=x onerror=...>` would run
 * on hover.
 *
 * TODO(ui-charts): promote this (or `echarts.format.encodeHTML` directly) to
 * `charts/core/format.ts` so other plot families writing custom tooltip formatters don't
 * have to rediscover this.
 */
import { echarts } from "./echarts";

export function escapeHtml(value: unknown): string {
  return echarts.format.encodeHTML(String(value ?? ""));
}
