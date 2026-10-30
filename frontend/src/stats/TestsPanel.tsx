import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { featureOn } from "../api/features";
import { runStatTest, STAT_TEST_LABELS, type StatTestKind } from "../api/stats";
import type { Column, Pipeline, SourceRef } from "../api/types";
import { Button } from "../components/ui/button";
import { Field, Input, Select } from "../components/ui/input";
import { FieldChecklist } from "./FieldChecklist";
import { StatResultView } from "./StatResultView";

/**
 * TODO(stats): hypothesis tests, regression, distribution fitting, outliers and confidence
 * intervals (MVP §5). The UI, field pickers and run flow are real; only the backend endpoint
 * (`runStatTest` in src/api/stats.ts) and its request/response shapes are provisional, pending
 * the `stats` agent's Wave 2 branch. Flip `features().statsTests` on once it merges, then widen
 * the per-test field/param pickers to whatever that branch's contracts actually ask for.
 */
export function TestsPanel({
  source,
  pipelines,
  columns,
}: {
  source: SourceRef | undefined;
  pipelines: Pipeline[];
  columns: Column[];
}) {
  const enabled = featureOn("statsTests");
  const [kind, setKind] = useState<StatTestKind>("correlation");
  const [fields, setFields] = useState<string[]>([]);
  const [groupBy, setGroupBy] = useState<string[]>([]);
  const [confidenceLevel, setConfidenceLevel] = useState("0.95");

  const run = useMutation({
    mutationFn: () =>
      runStatTest({
        kind,
        source: source as SourceRef,
        pipelines,
        fields,
        groupBy,
        params: { confidenceLevel: Number(confidenceLevel) },
      }),
  });

  const canRun = enabled && source !== undefined && fields.length > 0;

  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[280px_minmax(0,1fr)]">
      <form
        className="flex flex-col gap-3 rounded-md border border-line p-3"
        aria-label="Statistical test settings"
        onSubmit={(e) => {
          e.preventDefault();
          if (canRun) run.mutate();
        }}
      >
        {!enabled ? (
          <p
            role="note"
            className="rounded-md border border-dashed border-line p-2 text-xs text-muted"
          >
            Correlation, regression, hypothesis tests, distribution fitting, outlier detection and
            confidence intervals are coming soon (Wave 2). The form below previews the UI.
          </p>
        ) : null}
        <Field label="Test">
          {(p) => (
            <Select
              {...p}
              value={kind}
              disabled={!enabled}
              options={Object.entries(STAT_TEST_LABELS).map(([value, label]) => ({
                value,
                label,
              }))}
              onChange={(e) => setKind(e.target.value as StatTestKind)}
            />
          )}
        </Field>
        <FieldChecklist
          legend="Fields"
          columns={enabled ? columns : []}
          selected={fields}
          onChange={setFields}
          max={10}
          hint="Which fields the test uses depends on the test (e.g. two for correlation)."
        />
        <FieldChecklist
          legend="Group by (optional)"
          columns={enabled ? columns : []}
          selected={groupBy}
          onChange={setGroupBy}
          max={2}
        />
        <Field label="Confidence level" hint="Used by CIs and regression bands.">
          {(p) => (
            <Input
              {...p}
              type="number"
              min={0.5}
              max={0.999}
              step={0.01}
              disabled={!enabled}
              value={confidenceLevel}
              onChange={(e) => setConfidenceLevel(e.target.value)}
            />
          )}
        </Field>
        <Button type="submit" variant="primary" disabled={!canRun || run.isPending}>
          {run.isPending ? "Running…" : "Run"}
        </Button>
      </form>
      <div className="min-h-0 flex-1 overflow-auto" aria-live="polite">
        {run.isError ? (
          <p role="alert" className="text-sm text-danger">
            {run.error.message}
          </p>
        ) : run.data ? (
          <StatResultView result={run.data} />
        ) : (
          <p className="text-sm text-muted">
            {enabled ? "Pick a test and fields, then Run." : "Not available yet."}
          </p>
        )}
      </div>
    </div>
  );
}
