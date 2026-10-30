import type { EChartsCoreOption } from "echarts/core";
import { useEffect, useState } from "react";
import { OKABE_ITO, sampleRamp, VIRIDIS } from "./palettes";
import type { ChartTheme } from "./types";

const DIVERGING = ["#2166ac", "#67a9cf", "#d1e5f0", "#f7f7f7", "#fddbc7", "#ef8a62", "#b2182b"];

/** A numbered token ramp (`--viz-seq-1..n`), with fallbacks for the stops that are missing. */
function ramp(
  token: (name: string, fallback: string) => string,
  prefix: string,
  fallback: string[],
): string[] {
  return fallback.map((color, i) => token(`${prefix}-${i + 1}`, color));
}

/**
 * Font stacks come from CSS as `"Inter", system-ui`. ECharts writes the value into an SVG
 * attribute without escaping, so double quotes would make exported SVG invalid XML.
 */
export function svgSafeFont(fontFamily: string): string {
  return fontFamily.replace(/"/g, "'");
}

function isDarkDocument(): boolean {
  const forced = document.documentElement.dataset.theme;
  if (forced === "dark") return true;
  if (forced === "light") return false;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

/** Read chart tokens from CSS variables, deriving fallbacks for tokens ui-data hasn't defined. */
export function readChartTheme(element: Element = document.documentElement): ChartTheme {
  const style = getComputedStyle(element);
  const token = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
  const text = token("--viz-text", "#16181d");
  const border = token("--viz-border", "#dde1e7");
  const fontSans = token("--viz-font-sans", "system-ui, sans-serif");
  return {
    palette: OKABE_ITO.map((color, i) => token(`--viz-cat-${i + 1}`, color)),
    text,
    textMuted: token("--viz-text-muted", "#5b6270"),
    border,
    fontFamily: svgSafeFont(token("--viz-font-chart", fontSans)),
    mode: isDarkDocument() ? "dark" : "light",
    background: token("--viz-chart-bg", "transparent"),
    grid: token("--viz-chart-grid", border),
    axis: token("--viz-chart-axis", border),
    tooltipBg: token("--viz-chart-tooltip-bg", token("--viz-surface", "#ffffff")),
    tooltipText: token("--viz-chart-tooltip-text", text),
    sequential: ramp(token, "--viz-seq", sampleRamp(VIRIDIS, 9)),
    diverging: ramp(token, "--viz-div", DIVERGING),
  };
}

/** Fill the optional theme fields; renderers and primitives call this once. */
export function resolveTheme(theme: ChartTheme): Required<ChartTheme> {
  return {
    ...theme,
    fontFamily: svgSafeFont(theme.fontFamily),
    mode: theme.mode ?? "light",
    background: theme.background ?? "transparent",
    grid: theme.grid ?? theme.border,
    axis: theme.axis ?? theme.border,
    tooltipBg: theme.tooltipBg ?? (theme.mode === "dark" ? "#23272f" : "#ffffff"),
    tooltipText: theme.tooltipText ?? theme.text,
    sequential: theme.sequential && theme.sequential.length > 1 ? theme.sequential : VIRIDIS,
    diverging: theme.diverging && theme.diverging.length > 1 ? theme.diverging : DIVERGING,
  };
}

/** Current chart theme; updates when data-theme or the OS color scheme changes. */
export function useChartTheme(): ChartTheme {
  const [theme, setTheme] = useState(readChartTheme);
  useEffect(() => {
    const update = () => setTheme(readChartTheme());
    // The theme tokens change after the attribute flips, so read on the next frame.
    const observer = new MutationObserver(() => requestAnimationFrame(update));
    observer.observe(document.documentElement, { attributeFilter: ["data-theme", "class"] });
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    media?.addEventListener("change", update);
    return () => {
      observer.disconnect();
      media?.removeEventListener("change", update);
    };
  }, []);
  return theme;
}

/**
 * An ECharts theme object (for `chart.setTheme` / `echarts.init(el, theme)`). It styles every
 * chart in one place, so a light/dark switch needs no re-init.
 */
export function buildEChartsTheme(input: ChartTheme): Record<string, unknown> {
  const theme = resolveTheme(input);
  const axis = {
    axisLine: { lineStyle: { color: theme.axis } },
    axisTick: { lineStyle: { color: theme.axis } },
    axisLabel: { color: theme.textMuted },
    splitLine: { lineStyle: { color: theme.grid } },
    nameTextStyle: { color: theme.textMuted },
  };
  return {
    color: theme.palette,
    backgroundColor: theme.background,
    darkMode: theme.mode === "dark",
    textStyle: { color: theme.text, fontFamily: theme.fontFamily },
    title: {
      textStyle: { color: theme.text, fontFamily: theme.fontFamily },
      subtextStyle: { color: theme.textMuted, fontFamily: theme.fontFamily },
    },
    legend: { textStyle: { color: theme.text } },
    tooltip: {
      backgroundColor: theme.tooltipBg,
      borderColor: theme.border,
      textStyle: { color: theme.tooltipText },
      axisPointer: { lineStyle: { color: theme.axis }, crossStyle: { color: theme.axis } },
    },
    categoryAxis: axis,
    valueAxis: axis,
    logAxis: axis,
    timeAxis: axis,
    dataZoom: {
      textStyle: { color: theme.textMuted },
      borderColor: theme.border,
      backgroundColor: "transparent",
      fillerColor: `${theme.palette[0] ?? "#0072b2"}33`,
    },
    visualMap: { textStyle: { color: theme.textMuted } },
  };
}

export function baseOption(theme: ChartTheme): EChartsCoreOption {
  const t = resolveTheme(theme);
  return {
    color: t.palette,
    backgroundColor: t.background,
    textStyle: { color: t.text, fontFamily: t.fontFamily },
    aria: { enabled: true },
    animationDuration: 300,
  };
}

export function axisStyle(theme: ChartTheme): Record<string, unknown> {
  const t = resolveTheme(theme);
  return {
    axisLine: { lineStyle: { color: t.axis } },
    axisTick: { lineStyle: { color: t.axis } },
    axisLabel: { color: t.textMuted },
    splitLine: { lineStyle: { color: t.grid } },
    nameTextStyle: { color: t.textMuted },
  };
}
