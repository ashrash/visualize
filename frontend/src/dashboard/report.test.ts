import { describe, expect, it } from "vitest";
import {
  buildReportHtml,
  escapeHtml,
  paginateTiles,
  type ReportModel,
  type ReportTile,
} from "./report";

const PNG = "data:image/png;base64,iVBORw0KGgo=";

const tile = (over: Partial<ReportTile>): ReportTile => ({
  id: "t",
  x: 0,
  y: 0,
  w: 6,
  h: 4,
  title: "Sales",
  kind: "chart",
  ...over,
});

const model = (tiles: ReportTile[], over: Partial<ReportModel> = {}): ReportModel => ({
  title: "Q3 <review>",
  subtitle: "Demo project",
  generatedAt: "2026-10-31 12:00",
  filters: [],
  tabs: [{ name: "Overview", tiles }],
  ...over,
});

describe("buildReportHtml", () => {
  it("is a standalone document with no scripts, the title escaped and tiles placed on the grid", () => {
    const html = buildReportHtml(model([tile({ image: PNG, x: 6, y: 2, w: 3, h: 5 })]));
    expect(html.startsWith("<!doctype html>")).toBe(true);
    expect(html).toContain("<title>Q3 &lt;review&gt;</title>");
    expect(html).not.toMatch(/<script/i);
    expect(html).toContain("grid-column:7 / span 3;grid-row:3 / span 5");
    expect(html).toContain(`<img src="${PNG}"`);
    expect(html).toContain("@media print");
  });

  it("drops an image that is not a PNG data URL (no external requests, no injection)", () => {
    const html = buildReportHtml(
      model([
        tile({ image: 'https://evil.example/x.png" onerror="alert(1)' }),
        tile({ id: "b", image: "data:text/html,<script>alert(1)</script>" }),
      ]),
    );
    expect(html).not.toContain("evil.example");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("alert(1)");
  });

  it("sanitizes text tiles again and escapes KPI lines", () => {
    const html = buildReportHtml(
      model([
        tile({
          id: "a",
          kind: "text",
          html: "<p>ok</p><script>alert(1)</script><img src=x onerror=alert(2)>",
        }),
        tile({ id: "b", kind: "kpi", lines: ["Revenue", "<b>1.2M</b>"] }),
      ]),
    );
    expect(html).toContain("<p>ok</p>");
    expect(html).not.toMatch(/<script>alert|onerror/);
    expect(html).toContain("&lt;b&gt;1.2M&lt;/b&gt;");
  });

  it("starts each later tab on a new page, and lists the filters in effect", () => {
    const html = buildReportHtml(
      model([tile({ image: PNG })], {
        filters: ["region: North"],
        tabs: [
          { name: "One", tiles: [tile({ image: PNG })] },
          { name: "Two <b>", tiles: [tile({ image: PNG })] },
        ],
      }),
    );
    expect(html.match(/break-before:page/g)).toHaveLength(1);
    expect(html).toContain("<h2>Two &lt;b&gt;</h2>");
    expect(html).toContain("Filtered by: region: North");
  });
});

describe("escapeHtml", () => {
  it("escapes the five special characters", () => {
    expect(escapeHtml(`<a href="x">&'</a>`)).toBe(
      "&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;",
    );
  });
});

describe("paginateTiles", () => {
  it("keeps what fits on one page, and renumbers rows from the page top", () => {
    const pages = paginateTiles([tile({ id: "a", y: 0, h: 4 }), tile({ id: "b", y: 4, h: 4 })], 10);
    expect(pages).toHaveLength(1);
    expect(pages[0]?.map((t) => [t.id, t.y])).toEqual([
      ["a", 0],
      ["b", 4],
    ]);
  });

  it("moves a tile that would cross the page bottom to the next page", () => {
    const pages = paginateTiles(
      [
        tile({ id: "a", y: 0, h: 6 }),
        tile({ id: "b", y: 6, h: 6 }),
        tile({ id: "c", y: 12, h: 3 }),
      ],
      10,
    );
    expect(pages.map((p) => p.map((t) => t.id))).toEqual([["a"], ["b", "c"]]);
    expect(pages[1]?.map((t) => t.y)).toEqual([0, 6]);
  });

  it("keeps side-by-side tiles together and clamps a tile taller than a page", () => {
    const pages = paginateTiles([tile({ id: "a", x: 0, h: 4 }), tile({ id: "b", x: 6, h: 4 })], 10);
    expect(pages[0]).toHaveLength(2);
    const tall = paginateTiles([tile({ id: "z", h: 40 })], 10);
    expect(tall[0]?.[0]?.h).toBe(10);
  });

  it("returns no pages for no tiles", () => {
    expect(paginateTiles([], 10)).toEqual([]);
  });
});

describe("report text tiles", () => {
  it("drop images, so the document makes no external requests when opened", () => {
    const html = buildReportHtml(
      model([
        tile({
          kind: "text",
          html: '<p>see</p><img src="https://host.example/p.png" alt="x"><a href="https://ok.example">link</a>',
        }),
      ]),
    );
    expect(html).not.toContain("host.example");
    expect(html).not.toContain("<img");
    expect(html).toContain("<p>see</p>");
  });
});
