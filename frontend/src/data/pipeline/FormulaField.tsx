import { useMemo } from "react";
import type { StepError } from "../../api/types";
import { FormulaEditor } from "./FormulaEditor";
import type { FormContext } from "./StepForm";
import { useFormulaCheck, useFormulaFunctions } from "./usePipelineApi";

/** Formula editor plus the server's verdict: result type, or the error underlined in place. */
export function FormulaField({
  id,
  describedBy,
  label,
  value,
  onChange,
  stepId,
  ctx,
}: {
  id?: string;
  describedBy?: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  stepId: string;
  ctx: FormContext;
}) {
  const functions = useFormulaFunctions();
  const check = useFormulaCheck({
    expression: value,
    pipeline: ctx.pipeline,
    pipelines: ctx.pipelines ?? [],
    beforeStepId: stepId,
  });
  const hasText = value.trim() !== "";
  // An ok verdict from the server wins over an older step error from inspect.
  const error: StepError | null = !hasText
    ? null
    : check.data
      ? check.data.ok
        ? null
        : check.data.error
      : (ctx.stepError ?? null);
  const errorStart = error?.start ?? null;
  const errorEnd = error?.end ?? null;
  const errorMessage = error?.message ?? "";
  const range = useMemo(
    () =>
      errorStart !== null && errorEnd !== null
        ? { start: errorStart, end: errorEnd, message: errorMessage }
        : null,
    [errorStart, errorEnd, errorMessage],
  );
  return (
    <div className="flex flex-col gap-1">
      <FormulaEditor
        id={id}
        label={label}
        describedBy={describedBy}
        columns={ctx.columns}
        functions={functions.data}
        value={value}
        onChange={onChange}
        error={range}
      />
      {error ? (
        <p role="alert" className="text-xs text-danger">
          {error.message}
        </p>
      ) : hasText && check.data?.ok ? (
        <p className="text-xs text-muted">
          Valid{check.data.outputType ? `, result type ${check.data.outputType.toLowerCase()}` : ""}
        </p>
      ) : null}
    </div>
  );
}
