import type { ECharts } from "echarts/core";
import { echarts } from "../charts/core/echarts";

export type ExportFormat = "png" | "jpeg" | "svg" | "pdf" | "clipboard";

export interface ExportOptions {
  format: ExportFormat;
  /** File name without extension. */
  filename: string;
  /** Raster scale; 2 gives a crisp image on high-DPI screens. */
  pixelRatio?: number;
  /** Solid background for raster and PDF output (charts are transparent on screen). */
  background?: string;
  /** Printed above the chart in a PDF. */
  title?: string;
  /** ECharts theme object, so the offscreen SVG render matches the on-screen chart. */
  theme?: Record<string, unknown>;
}

/**
 * Where exported files go. The default sink uses the browser (download link, async clipboard);
 * a desktop shell can supply its own (native save dialog, native clipboard) without touching
 * any chart code.
 */
export interface ExportSink {
  save(blob: Blob, filename: string): Promise<void>;
  copyImage(blob: Blob): Promise<void>;
}

export const browserSink: ExportSink = {
  async save(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    // Revoke after the download has had a chance to start.
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  },
  async copyImage(blob) {
    if (typeof ClipboardItem === "undefined" || !navigator.clipboard?.write) {
      throw new Error(
        "Copying images needs a secure (https or localhost) page and a recent browser.",
      );
    }
    await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
  },
};

export const EXTENSION: Record<Exclude<ExportFormat, "clipboard">, string> = {
  png: "png",
  jpeg: "jpg",
  svg: "svg",
  pdf: "pdf",
};

const MIME: Record<Exclude<ExportFormat, "clipboard">, string> = {
  png: "image/png",
  jpeg: "image/jpeg",
  svg: "image/svg+xml",
  pdf: "application/pdf",
};

/** A file-system-safe name: letters, digits, dash, underscore; never empty. */
export function safeFilename(name: string): string {
  const cleaned = name
    .normalize("NFC")
    // Keep letters and digits of every script (not only ASCII), plus _ - and spaces.
    .replace(/[^\p{L}\p{N}_\- ]+/gu, "")
    .trim()
    .replace(/\s+/g, "-");
  return cleaned || "chart";
}

export function dataUrlToBlob(dataUrl: string): Blob {
  const [head, body = ""] = dataUrl.split(",");
  const mime = /data:([^;]+)/.exec(head ?? "")?.[1] ?? "application/octet-stream";
  const binary = atob(body);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

function rasterBlob(chart: ECharts, type: "png" | "jpeg", o: ExportOptions): Blob {
  const url = chart.getDataURL({
    type,
    pixelRatio: o.pixelRatio ?? 2,
    // JPEG has no alpha: without a solid background it would come out black.
    backgroundColor: o.background ?? (type === "jpeg" ? "#ffffff" : undefined),
  });
  return dataUrlToBlob(url);
}

/** Render the chart's current option as an SVG string, off screen (no layout, no flicker). */
export function chartToSvg(chart: ECharts, o: Pick<ExportOptions, "theme" | "background">): string {
  const width = chart.getWidth();
  const height = chart.getHeight();
  const offscreen = echarts.init(null, o.theme as never, {
    renderer: "svg",
    ssr: true,
    width,
    height,
  });
  try {
    offscreen.setOption({
      ...(chart.getOption() as Record<string, unknown>),
      animation: false,
      ...(o.background ? { backgroundColor: o.background } : {}),
    });
    return offscreen.renderToSVGString();
  } finally {
    offscreen.dispose();
  }
}

/**
 * jsPDF's built-in fonts only cover Latin-1: CJK, Cyrillic, Greek and symbols such as an arrow
 * come out as garbage. When the title or any chart text needs more, the PDF uses the raster
 * image instead of vector text, which renders every script correctly.
 *
 * Note that this also catches everyday characters just above Latin-1: the euro sign, the
 * ellipsis, and the U+202F narrow no-break space that Intl uses as a thousands separator in
 * fr and de number formats. Those charts export as raster PDFs; that is intended, not a bug.
 */
export function needsRasterPdf(svg: string, title?: string): boolean {
  const outsideLatin1 = /[Ā-\u{10ffff}]/u;
  return outsideLatin1.test(svg) || (title ? outsideLatin1.test(title) : false);
}

async function pdfBlob(chart: ECharts, o: ExportOptions): Promise<Blob> {
  // jsPDF and svg2pdf are large; load them only when someone exports a PDF.
  const [{ jsPDF }, { svg2pdf }] = await Promise.all([import("jspdf"), import("svg2pdf.js")]);
  const width = chart.getWidth();
  const height = chart.getHeight();
  const margin = 24;
  const svg = chartToSvg(chart, { theme: o.theme, background: o.background ?? "#ffffff" });
  const raster = needsRasterPdf(svg, o.title);
  // A rasterised PDF cannot print the title in a Latin-1 font, so it reserves no room for one
  // (the chart image carries its own title when the chart has one).
  const titleHeight = o.title && !raster ? 28 : 0;
  const doc = new jsPDF({
    orientation: width >= height ? "landscape" : "portrait",
    unit: "pt",
    format: [width + margin * 2, height + margin * 2 + titleHeight],
  });
  const x = margin;
  const y = margin + titleHeight;
  if (o.title && !raster) {
    doc.setFontSize(14);
    doc.text(o.title, margin, margin + 12);
  }
  const host = document.createElement("div");
  host.style.cssText = "position:fixed;left:-10000px;top:0;visibility:hidden";
  host.innerHTML = svg;
  document.body.appendChild(host);
  try {
    const element = host.querySelector("svg");
    if (!element || raster) throw new Error("raster");
    await svg2pdf(element, doc, { x, y, width, height });
  } catch {
    // Non-Latin text, or a failed vector conversion: use a raster image.
    const url = chart.getDataURL({
      type: "png",
      pixelRatio: 2,
      backgroundColor: o.background ?? "#ffffff",
    });
    doc.addImage(url, "PNG", x, y, width, height);
  } finally {
    host.remove();
  }
  return doc.output("blob");
}

/** The one export entry point: every format, the toolbar and (later) dashboards go through it. */
export async function saveChart(
  chart: ECharts,
  options: ExportOptions,
  sink: ExportSink = browserSink,
): Promise<void> {
  const { format } = options;
  if (format === "clipboard") {
    await sink.copyImage(rasterBlob(chart, "png", options));
    return;
  }
  let blob: Blob;
  switch (format) {
    case "png":
    case "jpeg":
      blob = rasterBlob(chart, format, options);
      break;
    case "svg":
      blob = new Blob([chartToSvg(chart, options)], { type: MIME.svg });
      break;
    case "pdf":
      blob = await pdfBlob(chart, options);
      break;
  }
  await sink.save(blob, `${safeFilename(options.filename)}.${EXTENSION[format]}`);
}
