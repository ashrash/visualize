import type { InputHTMLAttributes, LabelHTMLAttributes, ReactNode, Ref, SelectHTMLAttributes } from "react";
import { useId } from "react";
import { cn } from "./cn";

const fieldClass =
  "h-8 w-full min-w-0 rounded-md border border-line bg-bg px-2 text-sm text-fg placeholder:text-muted disabled:opacity-50";

export function Input({
  className,
  ref,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { ref?: Ref<HTMLInputElement> }) {
  return <input ref={ref} className={cn(fieldClass, className)} {...props} />;
}

export function Label({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  // biome-ignore lint/a11y/noLabelWithoutControl: association comes from htmlFor at the call site.
  return <label className={cn("text-xs font-medium text-muted", className)} {...props} />;
}

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

/**
 * Styled native select. Native keeps keyboard, screen-reader and mobile behaviour for free and
 * stays light; a Radix listbox adds nothing we need here.
 */
export function Select({
  options,
  className,
  ...props
}: Omit<SelectHTMLAttributes<HTMLSelectElement>, "children"> & { options: SelectOption[] }) {
  return (
    <select className={cn(fieldClass, "pr-6", className)} {...props}>
      {options.map((option) => (
        <option key={option.value} value={option.value} disabled={option.disabled}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

/** A label wired to its control through a generated id. */
export function Field({
  label,
  hint,
  className,
  children,
}: {
  label: string;
  hint?: string;
  className?: string;
  children: (props: { id: string; "aria-describedby"?: string }) => ReactNode;
}) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className={cn("flex min-w-0 flex-col gap-1", className)}>
      <Label htmlFor={id}>{label}</Label>
      {children({ id, "aria-describedby": hintId })}
      {hint ? (
        <p id={hintId} className="text-xs text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
