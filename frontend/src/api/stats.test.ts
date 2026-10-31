import { afterEach, describe, expect, it, vi } from "vitest";
import { describeFields, runStatTest, valuesOf } from "./stats";
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

describe("runStatTest", () => {
  const result: StatResult = {
    values: { fields: ["x", "y"] },
    method: "Pearson correlation",
    n: 100,
    sampled: false,
    warnings: [],
    nMissing: 0,
  };

  it("routes a correlation call to /api/stats/correlation", async () => {
    const fn = mockFetch(json(result));
    const got = await runStatTest({
      kind: "correlation",
      body: {
        source: { kind: "dataset", id: "d1" },
        fields: ["x", "y"],
        method: "pearson",
        missing: "pairwise",
        ciLevel: 0.95,
      },
    });
    expect(got).toEqual(result);
    const sent = await call(fn);
    expect(sent.url).toBe("/api/stats/correlation");
    expect(sent.method).toBe("POST");
    expect(JSON.parse(sent.text)).toMatchObject({ fields: ["x", "y"] });
  });

  it("routes chi-square to /api/stats/chisquare and ttest to /api/stats/ttest", async () => {
    const fn = mockFetch(json(result));
    await runStatTest({
      kind: "chisquare",
      body: {
        source: { kind: "dataset", id: "d1" },
        kind: "independence",
        field: "a",
        columnField: "b",
        correction: true,
        maxLevels: 50,
      },
    });
    expect((await call(fn)).url).toBe("/api/stats/chisquare");
    await runStatTest({
      kind: "ttest",
      body: {
        source: { kind: "dataset", id: "d1" },
        kind: "welch",
        field: "v",
        mu: 0,
        groupField: "g",
        alternative: "two_sided",
        ciLevel: 0.95,
      },
    });
    expect((await call(fn)).url).toBe("/api/stats/ttest");
  });

  it("narrows values with valuesOf", () => {
    expect(valuesOf<{ fields: string[] }>(result).fields).toEqual(["x", "y"]);
  });
});
