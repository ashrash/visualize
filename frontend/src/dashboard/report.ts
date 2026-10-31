/**
 * Dashboard report: a self-contained HTML document or a PDF of the dashboard as it looks right
 * now (current filters and selections), one section per tab.
 *
 * Charts are captured as PNG from the live ECharts instances (the data was already fetched,
 * capped and reduced for the screen), so a report never re-queries and never ships raw rows.
 */
import { getInstanceByDom } from "echarts/core";
import { sanitizeHtml } from "./markdown";

export interface ReportTile {
  id: string;
  /** Grid position and size in 12-column units. */
  x: number;
  y: number;
  w: number;
  h: number;
  title: string;
  kind: "chart" | "kpi" | "text" | "image" | "filter";
  /** A `data:image/png;base64,` URL (charts). */
  image?: string;
  /** Sanitized HTML (text widgets). */
  html?: string;
  /** Plain text lines (KPI cards, filters, images' alt text). */
  lines?: string[];
}

export interface ReportTab {
  name: string;
  tiles: ReportTile[];
}

export interface ReportModel {
  title: string;
  subtitle: string;
  generatedAt: string;
  /** Human labels of the filters and selections in effect. */
  filters: string[];
  tabs: ReportTab[];
}

/** Print rules and the fixed 12-column grid of the standalone report document. */
const REPORT_CSS = [
  "@media print{@page{margin:12mm}.report-tile{break-inside:avoid;page-break-inside:avoid}}",
  ".report-grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));grid-auto-rows:56px;gap:12px}",
  ".report-tile{overflow:hidden;border:1px solid #ccc;border-radius:8px;padding:6px;display:flex;flex-direction:column;gap:4px}",
  ".report-tile img{min-height:0;max-width:100%;object-fit:contain;flex:1}",
].join("\n");

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const PNG_DATA_URL = /^data:image\/png;base64,[A-Za-z0-9+/=]+$/;

function tileHtml(tile: ReportTile): string {
  const style = `grid-column:${tile.x + 1} / span ${tile.w};grid-row:${tile.y + 1} / span ${tile.h}`;
  let body = "";
  if (tile.image && PNG_DATA_URL.test(tile.image)) {
    body = `<img src="${tile.image}" alt="${escapeHtml(tile.title)}">`;
  } else if (tile.html) {
    body = `<div class="report-text">${sanitizeHtml(tile.html, { noImages: true })}</div>`;
  } else if (tile.lines?.length) {
    body = tile.lines.map((l) => `<p>${escapeHtml(l)}</p>`).join("");
  }
  const heading = tile.kind === "chart" ? `<h3>${escapeHtml(tile.title)}</h3>` : "";
  return `<section class="report-tile" style="${style}">${heading}${body}</section>`;
}

/** The report as one standalone HTML document (no scripts, no external requests). */
export function buildReportHtml(model: ReportModel): string {
  const tabs = model.tabs
    .map(
      (tab, i) =>
        `<section class="report-tab"${i > 0 ? ' style="break-before:page"' : ""}>` +
        (model.tabs.length > 1 ? `<h2>${escapeHtml(tab.name)}</h2>` : "") +
        `<div class="report-grid">${tab.tiles.map(tileHtml).join("")}</div></section>`,
    )
    .join("");
  const filters = model.filters.length
    ? `<p class="report-filters">Filtered by: ${model.filters.map(escapeHtml).join("; ")}</p>`
    : "";
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(model.title)}</title>
<style>
body{font:14px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#16181d;background:#fff;margin:24px}
h1{font-size:22px;margin:0}h2{font-size:16px;margin:16px 0 8px}h3{font-size:13px;margin:0;font-weight:600}
.report-meta,.report-filters{color:#5b6270;font-size:12px;margin:2px 0}
.report-tile p{margin:2px 0}
${REPORT_CSS}
</style>
</head>
<body>
<header><h1>${escapeHtml(model.title)}</h1>
<p class="report-meta">${escapeHtml(model.subtitle)} &middot; Generated ${escapeHtml(model.generatedAt)}</p>${filters}</header>
${tabs}
</body>
</html>
`;
}

/**
 * Split tiles into pages of `rowsPerPage` grid rows. A tile never straddles a page: one that
 * would cross the bottom starts the next page, and rows are renumbered from the page's top.
 */
export function paginateTiles(tiles: ReportTile[], rowsPerPage: number): ReportTile[][] {
  const pages: ReportTile[][] = [];
  const ordered = [...tiles].sort((a, b) => a.y - b.y || a.x - b.x);
  // Tiles starting on the same row stay together; a tall tile may push later rows down.
  let pageStart = 0;
  let page: ReportTile[] = [];
  for (const tile of ordered) {
    const height = Math.min(tile.h, rowsPerPage);
    if (tile.y - pageStart + height > rowsPerPage && page.length > 0) {
      pages.push(page);
      page = [];
      pageStart = tile.y;
    }
    page.push({ ...tile, y: tile.y - pageStart, h: height });
  }
  if (page.length > 0) pages.push(page);
  return pages;
}

const CHART_PIXEL_RATIO = 2;

/** Read the tiles of the rendered widgets under `root`, in grid order. */
export function captureTiles(
  root: HTMLElement,
  items: { i: string; x: number; y: number; w: number; h: number; kind: ReportTile["kind"] }[],
  background: string,
): ReportTile[] {
  const sections = [...root.querySelectorAll<HTMLElement>("section.dashboard-widget")];
  return items.map((item, index) => {
    const section = sections[index];
    const title = section?.getAttribute("aria-label") ?? "";
    const tile: ReportTile = {
      id: item.i,
      x: item.x,
      y: item.y,
      w: item.w,
      h: item.h,
      title,
      kind: item.kind,
    };
    if (!section) return tile;
    if (item.kind === "chart") {
      const el = section.querySelector<HTMLElement>("[_echarts_instance_]");
      const chart = el ? getInstanceByDom(el) : undefined;
      if (chart) {
        tile.image = chart.getDataURL({
          type: "png",
          pixelRatio: CHART_PIXEL_RATIO,
          backgroundColor: background,
        });
      }
    } else if (item.kind === "text") {
      tile.html = section.querySelector(".dashboard-text")?.innerHTML ?? "";
    } else {
      // innerText on purpose: it gives the rendered lines (value, delta, label), not markup.
      // biome-ignore lint/performance/useDomNodeTextContent: rendered text is the point
      tile.lines = section.innerText
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean);
    }
    return tile;
  });
}

/** A4 landscape PDF: the title, then each tab's tiles at their grid positions, paginated. */
export async function buildReportPdf(model: ReportModel): Promise<Blob> {
  // jsPDF is large; it is loaded only when someone asks for a PDF.
  const { jsPDF } = await import("jspdf");
  const doc = new jsPDF({ orientation: "landscape", unit: "pt", format: "a4" });
  const pageW = doc.internal.pageSize.getWidth();
  const pageH = doc.internal.pageSize.getHeight();
  const margin = 28;
  const gap = 6;
  const colW = (pageW - margin * 2) / 12;
  const rowH = colW * 0.6;
  let first = true;

  const header = (text: string, size: number, y: number) => {
    doc.setFontSize(size);
    doc.text(text, margin, y);
  };

  model.tabs.forEach((tab, tabIndex) => {
    const top = margin + (tabIndex === 0 ? 46 : 22);
    const rowsPerPage = Math.max(1, Math.floor((pageH - top - margin) / rowH));
    const pages = paginateTiles(tab.tiles, rowsPerPage);
    (pages.length === 0 ? [[]] : pages).forEach((tiles, pageIndex) => {
      if (!first) doc.addPage();
      first = false;
      if (tabIndex === 0 && pageIndex === 0) {
        header(model.title, 16, margin + 10);
        doc.setFontSize(9);
        doc.text(`${model.subtitle} - Generated ${model.generatedAt}`, margin, margin + 24);
        if (model.filters.length > 0) {
          doc.text(`Filtered by: ${model.filters.join("; ")}`, margin, margin + 36, {
            maxWidth: pageW - margin * 2,
          });
        }
      }
      if (model.tabs.length > 1 && pageIndex === 0) header(tab.name, 11, top - 8);
      for (const tile of tiles) {
        const x = margin + tile.x * colW;
        const y = top + tile.y * rowH;
        const w = tile.w * colW - gap;
        const h = tile.h * rowH - gap;
        doc.setDrawColor(200);
        doc.rect(x, y, w, h);
        if (tile.image && PNG_DATA_URL.test(tile.image)) {
          doc.addImage(tile.image, "PNG", x + 2, y + 2, w - 4, h - 4, undefined, "FAST");
        } else {
          const text = tile.lines?.join("\n") ?? stripTags(tile.html ?? "");
          doc.setFontSize(10);
          doc.text(text, x + 6, y + 14, { maxWidth: w - 12 });
        }
      }
    });
  });
  return doc.output("blob");
}

function stripTags(html: string): string {
  const el = document.createElement("div");
  el.innerHTML = sanitizeHtml(html);
  return el.textContent ?? "";
}
