import { afterEach, describe, expect, it, vi } from "vitest";
import { getSharedView, iframeSnippet, linkUrl } from "./api";

describe("share api helpers", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("builds a full link URL and an iframe snippet from the server's paths", () => {
    expect(linkUrl("/share/abc", "http://localhost:8000")).toBe("http://localhost:8000/share/abc");
    const snippet = iframeSnippet("/embed/abc", "http://localhost:8000");
    expect(snippet).toContain('src="http://localhost:8000/embed/abc"');
    expect(snippet).toMatch(/^<iframe /);
    expect(snippet).toContain('title="Embedded dashboard"');
  });

  it("resolves a token on the share or embed route, encoding it", async () => {
    const fetchMock = vi.fn(
      async (_url: string) => new Response(JSON.stringify({ kind: "dashboard" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await getSharedView("a/b", "embed");
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/embed/a%2Fb");
    await getSharedView("tok", "share");
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/share/tok");
  });

  it("turns the 404 for a bad token into an ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "share link not found" }), { status: 404 }),
      ),
    );
    await expect(getSharedView("nope", "share")).rejects.toMatchObject({
      status: 404,
      message: "share link not found",
    });
  });
});
