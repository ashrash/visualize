import { describe, expect, it } from "vitest";
import {
  CIVIDIS,
  OKABE_ITO,
  PALETTES,
  paletteColors,
  rampColor,
  sampleRamp,
  VIRIDIS,
} from "./palettes";
import { makeTheme } from "./testing";

describe("palettes", () => {
  it("ships the Okabe-Ito palette", () => {
    expect(OKABE_ITO).toEqual([
      "#0072b2",
      "#e69f00",
      "#009e73",
      "#cc79a7",
      "#56b4e9",
      "#d55e00",
      "#f0e442",
      "#000000",
    ]);
  });

  it("has color-blind-safe ramps with their known end points", () => {
    expect(VIRIDIS[0]).toBe("#440154");
    expect(VIRIDIS.at(-1)).toBe("#fde725");
    expect(CIVIDIS[0]).toBe("#00224e");
    expect(PALETTES.map((p) => p.id)).toEqual(
      expect.arrayContaining(["okabe-ito", "viridis", "cividis"]),
    );
  });

  it("interpolates along a ramp", () => {
    expect(rampColor(["#000000", "#ffffff"], 0)).toBe("#000000");
    expect(rampColor(["#000000", "#ffffff"], 1)).toBe("#ffffff");
    expect(rampColor(["#000000", "#ffffff"], 0.5)).toBe("#808080");
    expect(sampleRamp(VIRIDIS, 3)).toEqual([VIRIDIS[0], rampColor(VIRIDIS, 0.5), VIRIDIS.at(-1)]);
  });

  it("falls back to the theme palette and swaps black on dark backgrounds", () => {
    const light = makeTheme();
    expect(paletteColors(null, light)).toBe(light.palette);
    expect(paletteColors("default", light)).toBe(light.palette);
    expect(paletteColors("okabe-ito", light).at(-1)).toBe("#000000");
    const dark = makeTheme({ mode: "dark", textMuted: "#bbbbbb" });
    expect(paletteColors("okabe-ito", dark).at(-1)).toBe("#bbbbbb");
  });

  it("samples sequential palettes to the requested count", () => {
    expect(paletteColors("viridis", makeTheme(), 5)).toHaveLength(5);
  });
});
