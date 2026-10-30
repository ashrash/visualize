import type { EChartsCoreOption } from "echarts/core";
import { useEffect, useState } from "react";
import type { ChartTheme } from "./types";

const FALLBACK_PALETTE = [
  "#0072b2",
  "#e69f00",
  "#009e73",
  "#cc79a7",
  "#56b4e9",
  "#d55e00",
  "#f0e442",
  "#000000",
];

export function readChartTheme(element: Element = document.documentElement): ChartTheme {
  const style = getComputedStyle(element);
  const token = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
  return {
    palette: FALLBACK_PALETTE.map((color, i) => token(`--viz-cat-${i + 1}`, color)),
    text: token("--viz-text", "#16181d"),
    textMuted: token("--viz-text-muted", "#5b6270"),
    border: token("--viz-border", "#dde1e7"),
    fontFamily: token("--viz-font-sans", "system-ui, sans-serif"),
  };
}

/** Current chart theme; updates when data-theme or the OS color scheme changes. */
export function useChartTheme(): ChartTheme {
  const [theme, setTheme] = useState(readChartTheme);
  useEffect(() => {
    const update = () => setTheme(readChartTheme());
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributeFilter: ["data-theme"] });
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", update);
    return () => {
      observer.disconnect();
      media.removeEventListener("change", update);
    };
  }, []);
  return theme;
}

export function baseOption(theme: ChartTheme): EChartsCoreOption {
  return {
    color: theme.palette,
    backgroundColor: "transparent",
    textStyle: { color: theme.text, fontFamily: theme.fontFamily },
    aria: { enabled: true },
    animationDuration: 300,
  };
}

export function axisStyle(theme: ChartTheme): Record<string, unknown> {
  return {
    axisLine: { lineStyle: { color: theme.border } },
    axisTick: { lineStyle: { color: theme.border } },
    axisLabel: { color: theme.textMuted },
    splitLine: { lineStyle: { color: theme.border } },
    nameTextStyle: { color: theme.textMuted },
  };
}
