import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { runStatTest, STAT_TEST_LABELS, type StatTestKind } from "../api/stats";
import type { Column, Pipeline, SourceRef } from "../api/types";
import { Button } from "../components/ui/button";
import { Field, Input, Select } from "../components/ui/input";
import { FieldChecklist } from "./FieldChecklist";
import { TestResultView } from "./TestResults";
import { buildCall, emptyForm, type TestForm } from "./testRequest";

const opts = (pairs: [string, string][]) => pairs.map(([value, label]) => ({ value, label }));

const ALTERNATIVES = opts([
  ["two_sided", "Two-sided"],
  ["less", "Less"],
  ["greater", "Greater"],
]);

function ColumnSelect({
  label,
  columns,
  value,
  onChange,
  optional,
  hint,
}: {
  label: string;
  columns: Column[];
  value: string;
  onChange: (name: string) => void;
  optional?: boolean;
  hint?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      {(p) => (
        <Select
          {...p}
          value={value}
          options={[
            { value: "", label: optional ? "None" : "Choose a field…" },
            ...columns.map((c) => ({ value: c.name, label: c.name })),
          ]}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </Field>
  );
}

function Check({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-1.5 text-sm">
      <input
        type="checkbox"
        className="accent-accent"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      {label}
    </label>
  );
}

/** Statistical tests (MVP §5): one form per test, built from the endpoint's own request model. */
export function TestsPanel({
  source,
  pipelines,
  columns,
}: {
  source: SourceRef | undefined;
  pipelines: Pipeline[];
  columns: Column[];
}) {
  const [form, setForm] = useState<TestForm>(emptyForm());
  const set = <K extends keyof TestForm>(key: K, value: TestForm[K]) =>
    setForm((f) => ({ ...f, [key]: value }));
  const [ranKind, setRanKind] = useState<StatTestKind>("correlation");

  const built = source ? buildCall(form, source, pipelines, columns) : null;
  const reason = built === null ? "Choose a dataset first." : built.ok ? null : built.reason;

  const run = useMutation({
    mutationFn: (call: Parameters<typeof runStatTest>[0]) => runStatTest(call),
    onMutate: (call) => setRanKind(call.kind),
  });

  const { kind, variant } = form;
  const twoGroup =
    (kind === "ttest" && (variant === "welch" || variant === "student")) ||
    (kind === "ranktest" && variant === "mann_whitney");
  const showCi = ["correlation", "regression", "ttest", "anova", "ci"].includes(kind);
  const showAlt = kind === "ttest" || kind === "ranktest";

  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[280px_minmax(0,1fr)]">
      <form
        className="flex flex-col gap-3 overflow-auto rounded-md border border-line p-3"
        aria-label="Statistical test settings"
        onSubmit={(e) => {
          e.preventDefault();
          if (built?.ok) run.mutate(built.call);
        }}
      >
        <Field label="Test">
          {(p) => (
            <Select
              {...p}
              value={kind}
              options={Object.entries(STAT_TEST_LABELS).map(([value, label]) => ({
                value,
                label,
              }))}
              onChange={(e) => {
                const next = e.target.value as StatTestKind;
                setForm({ ...emptyForm(next), ciLevel: form.ciLevel });
              }}
            />
          )}
        </Field>

        {kind === "correlation" ? (
          <>
            <FieldChecklist
              legend="Fields"
              columns={columns}
              selected={form.fields}
              onChange={(f) => set("fields", f)}
              max={50}
              hint="At least two."
            />
            <Field label="Method">
              {(p) => (
                <Select
                  {...p}
                  value={variant}
                  options={opts([
                    ["pearson", "Pearson"],
                    ["spearman", "Spearman"],
                    ["kendall", "Kendall"],
                  ])}
                  onChange={(e) => set("variant", e.target.value)}
                />
              )}
            </Field>
            <Field label="Missing values">
              {(p) => (
                <Select
                  {...p}
                  value={form.missing}
                  options={opts([
                    ["pairwise", "Pairwise deletion"],
                    ["listwise", "Listwise deletion"],
                  ])}
                  onChange={(e) => set("missing", e.target.value as TestForm["missing"])}
                />
              )}
            </Field>
          </>
        ) : null}

        {kind === "regression" ? (
          <>
            <ColumnSelect
              label="Predict (y)"
              columns={columns}
              value={form.field}
              onChange={(v) => set("field", v)}
            />
            <FieldChecklist
              legend="Predictors (x)"
              columns={columns.filter((c) => c.name !== form.field)}
              selected={form.x}
              onChange={(x) => set("x", x)}
              max={20}
              hint="Categorical predictors become dummy variables."
            />
            <Check
              label="Robust (HC3) standard errors"
              checked={form.robust}
              onChange={(v) => set("robust", v)}
            />
          </>
        ) : null}

        {kind === "ttest" || kind === "ranktest" ? (
          <Field label="Variant">
            {(p) => (
              <Select
                {...p}
                value={variant}
                options={
                  kind === "ttest"
                    ? opts([
                        ["welch", "Two-sample (Welch)"],
                        ["student", "Two-sample (Student, equal variances)"],
                        ["paired", "Paired"],
                        ["one_sample", "One-sample"],
                      ])
                    : opts([
                        ["mann_whitney", "Mann-Whitney U (two groups)"],
                        ["wilcoxon", "Wilcoxon signed-rank"],
                        ["kruskal", "Kruskal-Wallis (3+ groups)"],
                      ])
                }
                onChange={(e) => set("variant", e.target.value)}
              />
            )}
          </Field>
        ) : null}

        {kind === "ttest" || kind === "ranktest" || kind === "anova" || kind === "fit" ? (
          <ColumnSelect
            label={kind === "anova" ? "Dependent field" : "Value field"}
            columns={columns}
            value={form.field}
            onChange={(v) => set("field", v)}
          />
        ) : null}

        {twoGroup || (kind === "ranktest" && variant === "kruskal") ? (
          <ColumnSelect
            label="Group field"
            columns={columns}
            value={form.group}
            onChange={(v) => set("group", v)}
          />
        ) : null}

        {twoGroup ? (
          <div className="grid grid-cols-2 gap-2">
            <Field label="Group A">
              {(p) => (
                <Input {...p} value={form.groupA} onChange={(e) => set("groupA", e.target.value)} />
              )}
            </Field>
            <Field label="Group B">
              {(p) => (
                <Input {...p} value={form.groupB} onChange={(e) => set("groupB", e.target.value)} />
              )}
            </Field>
          </div>
        ) : null}

        {(kind === "ttest" && variant === "paired") ||
        (kind === "ranktest" && variant === "wilcoxon") ? (
          <ColumnSelect
            label="Paired field"
            columns={columns}
            value={form.field2}
            onChange={(v) => set("field2", v)}
            optional={kind === "ranktest"}
            hint={
              kind === "ranktest"
                ? "Leave empty to test the field against the value below."
                : undefined
            }
          />
        ) : null}

        {(kind === "ttest" && (variant === "one_sample" || variant === "paired")) ||
        (kind === "ranktest" && variant === "wilcoxon") ? (
          <Field label="Hypothesised mean / center">
            {(p) => (
              <Input
                {...p}
                type="number"
                step="any"
                value={form.mu}
                onChange={(e) => set("mu", e.target.value)}
              />
            )}
          </Field>
        ) : null}

        {kind === "anova" ? (
          <>
            <ColumnSelect
              label="Factor"
              columns={columns}
              value={form.group}
              onChange={(v) => set("group", v)}
            />
            <ColumnSelect
              label="Second factor (two-way)"
              columns={columns}
              value={form.field2}
              onChange={(v) => set("field2", v)}
              optional
            />
            {form.field2 ? (
              <Check
                label="Include interaction"
                checked={form.interaction}
                onChange={(v) => set("interaction", v)}
              />
            ) : null}
          </>
        ) : null}

        {kind === "chisquare" ? (
          <>
            <Field label="Variant">
              {(p) => (
                <Select
                  {...p}
                  value={variant}
                  options={opts([
                    ["independence", "Independence (two fields)"],
                    ["goodness_of_fit", "Goodness of fit (uniform)"],
                  ])}
                  onChange={(e) => set("variant", e.target.value)}
                />
              )}
            </Field>
            <ColumnSelect
              label={variant === "independence" ? "Row field" : "Field"}
              columns={columns}
              value={form.field}
              onChange={(v) => set("field", v)}
            />
            {variant === "independence" ? (
              <ColumnSelect
                label="Column field"
                columns={columns}
                value={form.field2}
                onChange={(v) => set("field2", v)}
              />
            ) : null}
            <Check
              label="Yates correction (2×2 tables)"
              checked={form.correction}
              onChange={(v) => set("correction", v)}
            />
          </>
        ) : null}

        {kind === "outliers" || kind === "ci" ? (
          <>
            <FieldChecklist
              legend="Fields"
              columns={columns}
              selected={form.fields}
              onChange={(f) => set("fields", f)}
              max={20}
            />
            {kind === "outliers" ? (
              <>
                <Field label="Method">
                  {(p) => (
                    <Select
                      {...p}
                      value={variant}
                      options={opts([
                        ["iqr", "Tukey fences (IQR)"],
                        ["zscore", "z-score"],
                        ["mad", "Modified z-score (MAD)"],
                      ])}
                      onChange={(e) => set("variant", e.target.value)}
                    />
                  )}
                </Field>
                <Field label="Threshold" hint="Empty uses the method's default (1.5, 3 or 3.5).">
                  {(p) => (
                    <Input
                      {...p}
                      type="number"
                      min={0}
                      step="any"
                      value={form.threshold}
                      onChange={(e) => set("threshold", e.target.value)}
                    />
                  )}
                </Field>
              </>
            ) : (
              <>
                <Field label="Statistic">
                  {(p) => (
                    <Select
                      {...p}
                      value={form.statistic}
                      options={opts([
                        ["mean", "Mean"],
                        ["median", "Median"],
                        ["std", "Standard deviation"],
                        ["quantile", "Quantile"],
                      ])}
                      onChange={(e) => set("statistic", e.target.value as TestForm["statistic"])}
                    />
                  )}
                </Field>
                {form.statistic === "quantile" ? (
                  <Field label="Quantile (0 to 1)">
                    {(p) => (
                      <Input
                        {...p}
                        type="number"
                        min={0}
                        max={1}
                        step="any"
                        value={form.quantile}
                        onChange={(e) => set("quantile", e.target.value)}
                      />
                    )}
                  </Field>
                ) : null}
                <Field label="Method">
                  {(p) => (
                    <Select
                      {...p}
                      value={variant}
                      options={opts([
                        ["auto", "Automatic"],
                        ["t", "t (mean only)"],
                        ["bootstrap", "Bootstrap"],
                      ])}
                      onChange={(e) => set("variant", e.target.value)}
                    />
                  )}
                </Field>
              </>
            )}
            <ColumnSelect
              label="Group by"
              columns={columns}
              value={form.group}
              onChange={(v) => set("group", v)}
              optional
            />
          </>
        ) : null}

        {showAlt ? (
          <Field label="Alternative">
            {(p) => (
              <Select
                {...p}
                value={form.alternative}
                options={ALTERNATIVES}
                onChange={(e) => set("alternative", e.target.value as TestForm["alternative"])}
              />
            )}
          </Field>
        ) : null}

        {showCi ? (
          <Field label="Confidence level">
            {(p) => (
              <Input
                {...p}
                type="number"
                min={0.5}
                max={0.999}
                step={0.01}
                value={form.ciLevel}
                onChange={(e) => set("ciLevel", e.target.value)}
              />
            )}
          </Field>
        ) : null}

        {reason ? <p className="text-xs text-muted">{reason}</p> : null}
        <Button type="submit" variant="primary" disabled={!built?.ok || run.isPending}>
          {run.isPending ? "Running…" : "Run"}
        </Button>
      </form>
      <div className="min-h-0 flex-1 overflow-auto" aria-live="polite">
        {run.isError ? (
          <p role="alert" className="text-sm text-danger">
            {run.error.message}
          </p>
        ) : run.data ? (
          <TestResultView kind={ranKind} result={run.data} />
        ) : (
          <p className="text-sm text-muted">Pick a test and its fields, then Run.</p>
        )}
      </div>
    </div>
  );
}
