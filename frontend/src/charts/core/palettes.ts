import type { ChartTheme } from "./types";

/** Color palettes selectable through `style.palette`. All defaults are color-blind safe. */
export interface PaletteInfo {
  id: string;
  label: string;
  kind: "categorical" | "sequential";
  /** Categorical: the colors in order. Sequential: the ramp stops, low to high. */
  colors: string[];
}

export const OKABE_ITO = [
  "#0072b2",
  "#e69f00",
  "#009e73",
  "#cc79a7",
  "#56b4e9",
  "#d55e00",
  "#f0e442",
  "#000000",
];

export const TABLEAU_10 = [
  "#4e79a7",
  "#f28e2b",
  "#e15759",
  "#76b7b2",
  "#59a14f",
  "#edc948",
  "#b07aa1",
  "#ff9da7",
  "#9c755f",
  "#bab0ac",
];

export const VIRIDIS = [
  "#440154",
  "#482878",
  "#3e4a89",
  "#31688e",
  "#26828e",
  "#1f9e89",
  "#35b779",
  "#6ece58",
  "#b5de2b",
  "#fde725",
];

export const CIVIDIS = [
  "#00224e",
  "#123570",
  "#3b496c",
  "#575d6d",
  "#707173",
  "#8a8678",
  "#a59c74",
  "#c3b369",
  "#e1cc55",
  "#fee838",
];

export const PALETTES: PaletteInfo[] = [
  { id: "default", label: "Theme default", kind: "categorical", colors: [] },
  { id: "okabe-ito", label: "Okabe-Ito (color-blind safe)", kind: "categorical", colors: OKABE_ITO },
  { id: "tableau-10", label: "Tableau 10", kind: "categorical", colors: TABLEAU_10 },
  { id: "viridis", label: "Viridis (color-blind safe)", kind: "sequential", colors: VIRIDIS },
  { id: "cividis", label: "Cividis (color-blind safe)", kind: "sequential", colors: CIVIDIS },
];

function hexToRgb(hex: string): [number, number, number] {
  const value = Number.parseInt(hex.slice(1), 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

function rgbToHex([r, g, b]: [number, number, number]): string {
  return `#${[r, g, b].map((c) => Math.round(c).toString(16).padStart(2, "0")).join("")}`;
}

/** Color at position t (0..1) along a ramp of hex stops, interpolated in sRGB. */
export function rampColor(stops: string[], t: number): string {
  if (stops.length === 0) return "#000000";
  const first = stops[0] as string;
  if (stops.length === 1) return first;
  const position = Math.min(1, Math.max(0, t)) * (stops.length - 1);
  const index = Math.min(stops.length - 2, Math.floor(position));
  const frac = position - index;
  const a = hexToRgb(stops[index] as string);
  const b = hexToRgb(stops[index + 1] as string);
  return rgbToHex([a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac, a[2] + (b[2] - a[2]) * frac]);
}

/** n evenly spaced colors from a ramp (used when a sequential palette colors categories). */
export function sampleRamp(stops: string[], n: number): string[] {
  if (n <= 1) return [rampColor(stops, 0.5)];
  return Array.from({ length: n }, (_, i) => rampColor(stops, i / (n - 1)));
}

/**
 * Series colors for `style.palette` (or the theme's default when null/unknown).
 * Sequential palettes are sampled to `count` colors; categorical ones repeat in order.
 */
export function paletteColors(
  id: string | null | undefined,
  theme: ChartTheme,
  count = 8,
): string[] {
  const info = PALETTES.find((p) => p.id === id);
  if (!info || info.id === "default") return theme.palette;
  if (info.kind === "sequential") return sampleRamp(info.colors, Math.max(2, count));
  // Black disappears on a dark background; use the theme's muted text instead.
  return theme.mode === "dark"
    ? info.colors.map((c) => (c === "#000000" ? theme.textMuted : c))
    : info.colors;
}

/** Ramp for continuous color scales (heatmaps, density): the theme's, else viridis. */
export function sequentialRamp(theme: ChartTheme): string[] {
  return theme.sequential && theme.sequential.length > 1 ? theme.sequential : VIRIDIS;
}

export function divergingRamp(theme: ChartTheme): string[] {
  return theme.diverging && theme.diverging.length > 1
    ? theme.diverging
    : ["#2166ac", "#67a9cf", "#d1e5f0", "#f7f7f7", "#fddbc7", "#ef8a62", "#b2182b"];
}
