import { afterEach, describe, expect, it, vi } from "vitest";
import { describeFields, runStatTest } from "./stats";
import type { DescribeRequest, StatResult } from "./types";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function mockFetch(response: Response) {
  const fn = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => response.clone());
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

async function call(fn: ReturnType<typeof mockFetch>) {
  const [input] = fn.mock.calls.at(-1) ?? [undefined];
  if (input instanceof Request) {
    const url = new URL(input.url);
    return { url: url.pathname, method: input.method, text: await input.clone().text() };
  }
  throw new Error("expected a Request");
}

describe("describeFields", () => {
  it("posts to /api/stats/describe and returns the StatResult", async () => {
    const result: StatResult = {
      values: { summaries: [] },
      method: "describe",
      n: 10,
      sampled: false,
      warnings: [],
      nMissing: 0,
    };
    const fn = mockFetch(json(result));
    const body: DescribeRequest = {
      source: { kind: "dataset", id: "d1" },
      fields: ["age"],
      includeOverall: true,
      includeMode: true,
      maxGroups: 50,
    };
    const got = await describeFields(body);
    expect(got).toEqual(result);
    const sent = await call(fn);
    expect(sent.url).toBe("/api/stats/describe");
    expect(sent.method).toBe("POST");
    expect(JSON.parse(sent.text)).toMatchObject({ fields: ["age"] });
  });
});

describe("runStatTest (TODO(stats) stub)", () => {
  it("posts to /api/stats/<kind> with the test request", async () => {
    const result: StatResult = {
      values: { estimate: 0.5, statistic: 2.1, pValue: 0.04 },
      method: "Pearson correlation",
      n: 100,
      sampled: false,
      warnings: [],
      nMissing: 0,
    };
    const fn = vi.fn(
      async (_url: string, _init?: RequestInit) =>
        new Response(JSON.stringify(result), { status: 200 }),
    );
    vi.stubGlobal("fetch", fn);
    const got = await runStatTest({
      kind: "correlation",
      source: { kind: "dataset", id: "d1" },
      fields: ["x", "y"],
    });
    expect(got).toEqual(result);
    const [url, init] = fn.mock.calls.at(-1) ?? ["", undefined];
    expect(url).toBe("/api/stats/correlation");
    expect((init as RequestInit).method).toBe("POST");
  });
});
