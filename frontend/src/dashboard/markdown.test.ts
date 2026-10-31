import { describe, expect, it } from "vitest";
import { renderMarkdown } from "./markdown";

describe("renderMarkdown", () => {
  it("renders headings, emphasis, lists and tables", () => {
    const html = renderMarkdown("# Title\n\n**bold** and *em*\n\n- a\n- b\n\n| h |\n|---|\n| v |");
    expect(html).toContain("<h1>Title</h1>");
    expect(html).toContain("<strong>bold</strong>");
    expect(html).toContain("<li>a</li>");
    expect(html).toContain("<table>");
  });

  it("strips scripts, event handlers and iframes", () => {
    const html = renderMarkdown(
      '<script>alert(1)</script>hi <img src="x" onerror="alert(2)"> <iframe src="https://evil"></iframe>',
    );
    expect(html).not.toMatch(/<script|onerror|<iframe|alert/);
    expect(html).toContain("hi");
  });

  it("removes javascript: and data: URLs", () => {
    const html = renderMarkdown(
      "[bad](javascript:alert(1)) [data](data:text/html,<b>x</b>) [ok](https://example.com)",
    );
    expect(html).not.toContain("javascript:");
    expect(html).not.toContain("data:text");
    expect(html).toContain('href="https://example.com"');
  });

  it("opens links safely in a new tab", () => {
    const html = renderMarkdown("[a](https://example.com)");
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).toContain('target="_blank"');
  });

  it("drops style attributes and unknown tags but keeps their text", () => {
    const html = renderMarkdown('<div style="position:fixed">kept</div><form action="/x">f</form>');
    expect(html).not.toContain("style=");
    expect(html).not.toContain("<form");
    expect(html).toContain("kept");
  });
});
