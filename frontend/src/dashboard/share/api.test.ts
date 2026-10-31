import { afterEach, describe, expect, it, vi } from "vitest";
import { getSharedView, iframeSnippet, linkUrl, revokeShare } from "./api";

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
      async (_request: Request) =>
        new Response(JSON.stringify({ kind: "dashboard" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await getSharedView("a/b", "embed");
    expect(new URL(fetchMock.mock.calls[0]?.[0].url ?? "").pathname).toBe("/api/embed/a%2Fb");
    await getSharedView("tok", "share");
    expect(new URL(fetchMock.mock.calls[1]?.[0].url ?? "").pathname).toBe("/api/share/tok");
  });

  it("turns the 404 for a bad token into an ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "share link not found" }), {
            status: 404,
            headers: { "content-type": "application/json" },
          }),
      ),
    );
    await expect(getSharedView("nope", "share")).rejects.toMatchObject({
      status: 404,
      message: "share link not found",
    });
  });

  it("revoking succeeds on the 204 with no body", async () => {
    const calls: Request[] = [];
    vi.stubGlobal("fetch", async (request: Request) => {
      calls.push(request);
      return new Response(null, { status: 204 });
    });
    await expect(revokeShare("tok")).resolves.toBeUndefined();
    expect(calls[0]?.method).toBe("DELETE");
    expect(new URL(calls[0]?.url ?? "").pathname).toBe("/api/share/tok");
  });

  it("revoking still reports a real failure", async () => {
    vi.stubGlobal(
      "fetch",
      async () =>
        new Response(JSON.stringify({ detail: "share link not found" }), {
          status: 404,
          headers: { "content-type": "application/json" },
        }),
    );
    await expect(revokeShare("gone")).rejects.toMatchObject({ status: 404 });
  });
});
