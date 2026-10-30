// @vitest-environment jsdom
import type { ECharts } from "echarts/core";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { echarts } from "../charts/core/echarts";
import { getPlot } from "../charts/core/registry";
import { makeData, makeSpec, makeTheme } from "../charts/core/testing";
import { svgSafeFont } from "../charts/core/theme";
import { chartToSvg, dataUrlToBlob, type ExportSink, safeFilename, saveChart } from "./saveChart";

// 1x1 transparent PNG
const PNG =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";

function fakeChart() {
  const getDataURL = vi.fn(() => PNG);
  return {
    chart: {
      getDataURL,
      getWidth: () => 100,
      getHeight: () => 50,
      getOption: () => ({}),
    } as unknown as ECharts,
    getDataURL,
  };
}

function fakeSink() {
  const sink: ExportSink = { save: vi.fn(async () => {}), copyImage: vi.fn(async () => {}) };
  return sink;
}

describe("saveChart", () => {
  let sink: ExportSink;
  beforeEach(() => {
    sink = fakeSink();
  });

  it("saves a PNG at 2x with a safe file name", async () => {
    const { chart, getDataURL } = fakeChart();
    await saveChart(chart, { format: "png", filename: "Sales / Q4: review!" }, sink);
    expect(getDataURL).toHaveBeenCalledWith({
      type: "png",
      pixelRatio: 2,
      backgroundColor: undefined,
    });
    const [blob, name] = (sink.save as ReturnType<typeof vi.fn>).mock.calls[0] as [Blob, string];
    expect(name).toBe("Sales-Q4-review.png");
    expect(blob.type).toBe("image/png");
    expect(blob.size).toBeGreaterThan(0);
  });

  it("gives JPEGs an opaque background and the .jpg extension", async () => {
    const { chart, getDataURL } = fakeChart();
    await saveChart(chart, { format: "jpeg", filename: "c" }, sink);
    expect(getDataURL).toHaveBeenCalledWith(
      expect.objectContaining({ type: "jpeg", backgroundColor: "#ffffff" }),
    );
    expect((sink.save as ReturnType<typeof vi.fn>).mock.calls[0]?.[1]).toBe("c.jpg");
  });

  it("copies a PNG to the clipboard through the sink, not the file saver", async () => {
    const { chart } = fakeChart();
    await saveChart(chart, { format: "clipboard", filename: "c" }, sink);
    expect(sink.copyImage).toHaveBeenCalledTimes(1);
    expect(sink.save).not.toHaveBeenCalled();
  });

  it("propagates sink failures so the menu can show them", async () => {
    const { chart } = fakeChart();
    sink.copyImage = vi.fn(async () => {
      throw new Error("Copying images needs a secure page");
    });
    await expect(saveChart(chart, { format: "clipboard", filename: "c" }, sink)).rejects.toThrow(
      "secure page",
    );
  });
});

describe("SVG export", () => {
  it("produces well-formed XML for a real chart, even with a quoted font stack", () => {
    const source = echarts.init(null, undefined, {
      renderer: "svg",
      ssr: true,
      width: 300,
      height: 200,
    });
    const option = {
      ...(renderPieOption() as Record<string, unknown>),
    };
    source.setOption(option);
    const svg = chartToSvg(source, {});
    const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
    expect(doc.querySelector("parsererror")).toBeNull();
    expect(doc.documentElement.tagName).toBe("svg");
    expect(svg).toContain("<text");
    source.dispose();
  });
});

function renderPieOption() {
  const entry = getPlot("pie");
  if (!entry) throw new Error("pie missing");
  const theme = makeTheme({ fontFamily: svgSafeFont('"Inter", ui-sans-serif, system-ui') });
  const data = makeData({ x: ["a", "b"], y0: [1, 2] }, { x: "k", y0: "v" });
  return entry.render(
    makeSpec({
      plotType: "pie",
      style: { orientation: "vertical", stack: "none", showLabels: true },
    }),
    data,
    theme,
  );
}

describe("helpers", () => {
  it("makes safe, non-empty file names", () => {
    expect(safeFilename("  Q4 sales / EU  ")).toBe("Q4-sales-EU");
    expect(safeFilename("///")).toBe("chart");
    expect(safeFilename("Ünïcode")).toBe("Unicode");
  });

  it("decodes data URLs to blobs", () => {
    const blob = dataUrlToBlob(PNG);
    expect(blob.type).toBe("image/png");
    expect(blob.size).toBeGreaterThan(50);
  });
});
