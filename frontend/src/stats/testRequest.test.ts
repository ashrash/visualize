import { describe, expect, it } from "vitest";
import type { Column } from "../api/types";
import { buildCall, emptyForm, type TestForm } from "./testRequest";

const source = { kind: "dataset", id: "d1" } as const;
const columns: Column[] = [
  { name: "g", type: "BIGINT", semantic: "quantitative", nullCount: 0, approxDistinct: 3 },
  { name: "v", type: "DOUBLE", semantic: "quantitative", nullCount: 0, approxDistinct: 50 },
];

const form = (patch: Partial<TestForm>): TestForm => ({ ...emptyForm(), ...patch });
const build = (f: TestForm) => buildCall(f, source, [], columns);

describe("buildCall", () => {
  it("needs two fields for a correlation", () => {
    expect(build(form({ fields: ["v"] })).ok).toBe(false);
    const r = build(form({ fields: ["v", "g"], variant: "spearman" }));
    expect(r.ok && r.call.kind === "correlation" && r.call.body.method).toBe("spearman");
  });

  it("splits regression into y and x, never using y as a predictor", () => {
    const r = build(form({ kind: "regression", field: "v", x: ["v", "g"] }));
    expect(r.ok && r.call.kind === "regression" && r.call.body).toMatchObject({
      y: "v",
      x: ["g"],
    });
    expect(build(form({ kind: "regression", field: "v", x: ["v"] })).ok).toBe(false);
  });

  it("builds a two-sample t-test with numeric group values for a numeric group column", () => {
    const r = build(
      form({ kind: "ttest", variant: "welch", field: "v", group: "g", groupA: "1", groupB: "2" }),
    );
    expect(r.ok && r.call.kind === "ttest" && r.call.body).toMatchObject({
      kind: "welch",
      groupField: "g",
      groups: [1, 2],
    });
    expect(
      build(form({ kind: "ttest", variant: "welch", field: "v", group: "g", groupA: "1" })).ok,
    ).toBe(false);
  });

  it("requires a paired field for paired t-tests and none for one-sample", () => {
    expect(build(form({ kind: "ttest", variant: "paired", field: "v" })).ok).toBe(false);
    expect(build(form({ kind: "ttest", variant: "one_sample", field: "v" })).ok).toBe(true);
  });

  it("chi-square independence needs two distinct fields; goodness of fit needs one", () => {
    expect(build(form({ kind: "chisquare", variant: "independence", field: "g" })).ok).toBe(false);
    const gof = build(form({ kind: "chisquare", variant: "goodness_of_fit", field: "g" }));
    expect(gof.ok && gof.call.kind === "chisquare" && gof.call.body.columnField).toBeNull();
  });

  it("ANOVA takes one or two distinct factors", () => {
    expect(build(form({ kind: "anova", field: "v" })).ok).toBe(false);
    expect(build(form({ kind: "anova", field: "v", group: "g", field2: "g" })).ok).toBe(false);
    const r = build(form({ kind: "anova", field: "v", group: "g" }));
    expect(r.ok && r.call.kind === "anova" && r.call.body.factors).toEqual(["g"]);
  });

  it("rejects an out-of-range confidence level", () => {
    expect(build(form({ fields: ["v", "g"], ciLevel: "1.5" })).ok).toBe(false);
  });

  it("only sends a quantile for the quantile statistic", () => {
    const r = build(form({ kind: "ci", fields: ["v"], statistic: "mean" }));
    expect(r.ok && r.call.kind === "ci" && r.call.body.quantile).toBeNull();
  });
});
