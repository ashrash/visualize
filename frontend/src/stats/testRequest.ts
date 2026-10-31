import type { StatTestCall, StatTestKind } from "../api/stats";
import type { Column, Pipeline, SourceRef } from "../api/types";

/** Everything the tests form can hold; each test reads only the entries it needs. */
export interface TestForm {
  kind: StatTestKind;
  /** Multi-field tests: correlation, outliers, confidence intervals. */
  fields: string[];
  /** The main field: y (regression), value (t-test, rank, ANOVA, fit), first categorical. */
  field: string;
  /** Second field: paired field (t-test, Wilcoxon), column field (chi-square), ANOVA factor 2. */
  field2: string;
  /** Grouping field: t-test/rank groups, ANOVA factor 1, outlier and CI group-by. */
  group: string;
  /** Regression predictors. */
  x: string[];
  /** Sub-kind or method (t-test kind, rank kind, chi-square kind, correlation/CI method...). */
  variant: string;
  groupA: string;
  groupB: string;
  mu: string;
  alternative: "two_sided" | "less" | "greater";
  ciLevel: string;
  missing: "pairwise" | "listwise";
  statistic: "mean" | "median" | "std" | "quantile";
  quantile: string;
  threshold: string;
  robust: boolean;
  interaction: boolean;
  correction: boolean;
}

export const DEFAULT_VARIANT: Record<StatTestKind, string> = {
  correlation: "pearson",
  regression: "ols",
  ttest: "welch",
  ranktest: "mann_whitney",
  anova: "",
  chisquare: "independence",
  fit: "",
  outliers: "iqr",
  ci: "auto",
};

export const emptyForm = (kind: StatTestKind = "correlation"): TestForm => ({
  kind,
  fields: [],
  field: "",
  field2: "",
  group: "",
  x: [],
  variant: DEFAULT_VARIANT[kind],
  groupA: "",
  groupB: "",
  mu: "0",
  alternative: "two_sided",
  ciLevel: "0.95",
  missing: "pairwise",
  statistic: "mean",
  quantile: "0.5",
  threshold: "",
  robust: false,
  interaction: true,
  correction: true,
});

export type BuiltCall = { ok: true; call: StatTestCall } | { ok: false; reason: string };

const bad = (reason: string): BuiltCall => ({ ok: false, reason });

/** A group value typed in a text box, coerced to a number when the group column is numeric. */
function scalarFor(text: string, column: Column | undefined): string | number {
  if (column?.semantic === "quantitative") {
    const n = Number(text);
    if (Number.isFinite(n)) return n;
  }
  return text;
}

const ciOf = (form: TestForm) => Number(form.ciLevel);
const validCi = (form: TestForm) => ciOf(form) > 0 && ciOf(form) < 1;

/**
 * Turns the form into the request the matching endpoint wants (see `contracts/stats.py`), or
 * says what is still missing. Pure, so the per-test required-field rules are unit-tested.
 */
export function buildCall(
  form: TestForm,
  source: SourceRef,
  pipelines: Pipeline[],
  columns: Column[],
): BuiltCall {
  const base = { source, pipelines };
  const col = (name: string) => columns.find((c) => c.name === name);
  if (!validCi(form) && ["correlation", "regression", "ttest", "anova", "ci"].includes(form.kind)) {
    return bad("Confidence level must be between 0 and 1.");
  }
  const group2 = (): [string | number, string | number] | null =>
    form.groupA !== "" && form.groupB !== "" && form.groupA !== form.groupB
      ? [scalarFor(form.groupA, col(form.group)), scalarFor(form.groupB, col(form.group))]
      : null;

  switch (form.kind) {
    case "correlation":
      if (form.fields.length < 2) return bad("Pick at least two fields.");
      return {
        ok: true,
        call: {
          kind: "correlation",
          body: {
            ...base,
            fields: form.fields,
            method: form.variant as "pearson" | "spearman" | "kendall",
            missing: form.missing,
            ciLevel: ciOf(form),
          },
        },
      };
    case "regression": {
      if (!form.field) return bad("Pick the field to predict (y).");
      const x = form.x.filter((f) => f !== form.field);
      if (x.length === 0) return bad("Pick at least one predictor (x).");
      return {
        ok: true,
        call: {
          kind: "regression",
          body: {
            ...base,
            y: form.field,
            x,
            intercept: true,
            robust: form.robust,
            ciLevel: ciOf(form),
            maxLevels: 50,
          },
        },
      };
    }
    case "ttest": {
      if (!form.field) return bad("Pick the value field.");
      const variant = form.variant as "one_sample" | "welch" | "student" | "paired";
      const body = {
        ...base,
        kind: variant,
        field: form.field,
        mu: Number(form.mu) || 0,
        alternative: form.alternative,
        ciLevel: ciOf(form),
      };
      if (variant === "paired") {
        if (!form.field2) return bad("Pick the paired field.");
        return { ok: true, call: { kind: "ttest", body: { ...body, pairedField: form.field2 } } };
      }
      if (variant === "one_sample") return { ok: true, call: { kind: "ttest", body } };
      if (!form.group) return bad("Pick the grouping field.");
      const groups = group2();
      if (!groups) return bad("Enter two different group values to compare.");
      return {
        ok: true,
        call: { kind: "ttest", body: { ...body, groupField: form.group, groups } },
      };
    }
    case "ranktest": {
      if (!form.field) return bad("Pick the value field.");
      const variant = form.variant as "mann_whitney" | "wilcoxon" | "kruskal";
      const body = {
        ...base,
        kind: variant,
        field: form.field,
        mu: Number(form.mu) || 0,
        alternative: form.alternative,
        maxGroups: 50,
      };
      if (variant === "wilcoxon") {
        return {
          ok: true,
          call: {
            kind: "ranktest",
            body: form.field2 ? { ...body, pairedField: form.field2 } : body,
          },
        };
      }
      if (!form.group) return bad("Pick the grouping field.");
      if (variant === "kruskal") {
        return { ok: true, call: { kind: "ranktest", body: { ...body, groupField: form.group } } };
      }
      const groups = group2();
      if (!groups) return bad("Enter two different group values to compare.");
      return {
        ok: true,
        call: { kind: "ranktest", body: { ...body, groupField: form.group, groups } },
      };
    }
    case "anova": {
      if (!form.field) return bad("Pick the dependent field.");
      if (!form.group) return bad("Pick a factor.");
      const factors = form.field2 ? [form.group, form.field2] : [form.group];
      if (new Set(factors).size !== factors.length) return bad("The two factors must differ.");
      return {
        ok: true,
        call: {
          kind: "anova",
          body: {
            ...base,
            field: form.field,
            factors,
            interaction: form.interaction,
            ssType: 2,
            postHoc: true,
            ciLevel: ciOf(form),
            maxLevels: 50,
          },
        },
      };
    }
    case "chisquare": {
      if (!form.field) return bad("Pick the first categorical field.");
      const independence = form.variant === "independence";
      if (independence && !form.field2) return bad("Pick the second categorical field.");
      if (independence && form.field2 === form.field) return bad("The two fields must differ.");
      return {
        ok: true,
        call: {
          kind: "chisquare",
          body: {
            ...base,
            kind: independence ? "independence" : "goodness_of_fit",
            field: form.field,
            columnField: independence ? form.field2 : null,
            correction: form.correction,
            maxLevels: 50,
          },
        },
      };
    }
    case "fit":
      if (!form.field) return bad("Pick a numeric field.");
      return {
        ok: true,
        call: { kind: "fit", body: { ...base, field: form.field, gridSize: 200 } },
      };
    case "outliers": {
      if (form.fields.length === 0) return bad("Pick at least one field.");
      const threshold = form.threshold === "" ? null : Number(form.threshold);
      if (threshold !== null && !(threshold > 0)) return bad("Threshold must be positive.");
      return {
        ok: true,
        call: {
          kind: "outliers",
          body: {
            ...base,
            fields: form.fields,
            method: form.variant as "iqr" | "zscore" | "mad",
            threshold,
            groupBy: form.group || null,
            maxGroups: 50,
            maxRows: 100,
            include: [],
          },
        },
      };
    }
    case "ci": {
      if (form.fields.length === 0) return bad("Pick at least one field.");
      const quantile = Number(form.quantile);
      if (form.statistic === "quantile" && !(quantile > 0 && quantile < 1)) {
        return bad("Quantile must be between 0 and 1.");
      }
      return {
        ok: true,
        call: {
          kind: "ci",
          body: {
            ...base,
            fields: form.fields,
            statistic: form.statistic,
            quantile: form.statistic === "quantile" ? quantile : null,
            method: form.variant as "auto" | "t" | "bootstrap",
            ciLevel: ciOf(form),
            groupBy: form.group || null,
            maxGroups: 50,
            nResamples: 2000,
          },
        },
      };
    }
  }
}
