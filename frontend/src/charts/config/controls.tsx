import { type ReactNode, useEffect, useId, useState } from "react";

export const inputClass =
  "w-full rounded-md border border-line bg-bg px-2 py-1 text-sm text-fg focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50";

interface FieldProps {
  label: string;
  hint?: string;
  children: (id: string) => ReactNode;
}

/** A labelled control. The label is wired to the control through `id`, so it is accessible. */
export function Field({ label, hint, children }: FieldProps) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-muted">
        {label}
      </label>
      {children(id)}
      {hint ? <p className="text-xs text-muted">{hint}</p> : null}
    </div>
  );
}

interface SelectFieldProps {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange(value: string): void;
  hint?: string;
  disabled?: boolean;
}

export function SelectField({ label, value, options, onChange, hint, disabled }: SelectFieldProps) {
  return (
    <Field label={label} hint={hint}>
      {(id) => (
        <select
          id={id}
          className={inputClass}
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      )}
    </Field>
  );
}

interface TextFieldProps {
  label: string;
  value: string | null | undefined;
  onChange(value: string): void;
  placeholder?: string;
  hint?: string;
}

export function TextField({ label, value, onChange, placeholder, hint }: TextFieldProps) {
  return (
    <Field label={label} hint={hint}>
      {(id) => (
        <input
          id={id}
          type="text"
          className={inputClass}
          value={value ?? ""}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </Field>
  );
}

interface NumberFieldProps {
  label: string;
  value: number | null | undefined;
  /** Called with the parsed number, or null when the box is emptied. */
  onChange(value: number | null): void;
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
  hint?: string;
}

/** Keeps what the user typed ("1.", "-") until it parses, so typing is never fought. */
export function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step,
  placeholder,
  hint,
}: NumberFieldProps) {
  const [draft, setDraft] = useState(value === null || value === undefined ? "" : String(value));
  useEffect(() => {
    setDraft((current) => {
      const parsed = current === "" ? null : Number(current);
      return parsed === (value ?? null)
        ? current
        : value === null || value === undefined
          ? ""
          : String(value);
    });
  }, [value]);
  return (
    <Field label={label} hint={hint}>
      {(id) => (
        <input
          id={id}
          type="number"
          className={inputClass}
          value={draft}
          min={min}
          max={max}
          step={step}
          placeholder={placeholder}
          onChange={(e) => {
            setDraft(e.target.value);
            if (e.target.value === "") onChange(null);
            else if (Number.isFinite(Number(e.target.value))) onChange(Number(e.target.value));
          }}
        />
      )}
    </Field>
  );
}

interface CheckboxFieldProps {
  label: string;
  checked: boolean;
  onChange(checked: boolean): void;
  hint?: string;
}

export function CheckboxField({ label, checked, onChange, hint }: CheckboxFieldProps) {
  const id = useId();
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-center gap-2">
        <input
          id={id}
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
          className="size-4 accent-accent focus-visible:outline-2 focus-visible:outline-accent"
        />
        <label htmlFor={id} className="text-sm text-fg">
          {label}
        </label>
      </div>
      {hint ? <p className="pl-6 text-xs text-muted">{hint}</p> : null}
    </div>
  );
}

export function ColorField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string | null | undefined;
  onChange(value: string | null): void;
}) {
  return (
    <Field label={label}>
      {(id) => (
        <div className="flex items-center gap-2">
          <input
            id={id}
            type="color"
            value={value ?? "#0072b2"}
            onChange={(e) => onChange(e.target.value)}
            className="h-7 w-10 cursor-pointer rounded border border-line bg-bg p-0.5"
          />
          {value ? (
            <button
              type="button"
              onClick={() => onChange(null)}
              className="text-xs text-muted underline hover:text-fg focus-visible:outline-2 focus-visible:outline-accent"
            >
              Reset
            </button>
          ) : (
            <span className="text-xs text-muted">Automatic</span>
          )}
        </div>
      )}
    </Field>
  );
}

/** A collapsible group of related controls (native details: keyboard and screen-reader friendly). */
export function Section({
  title,
  children,
  defaultOpen = false,
}: {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details open={defaultOpen} className="group rounded-md border border-line">
      <summary className="cursor-pointer select-none rounded-md px-3 py-2 text-sm font-medium text-fg hover:bg-surface focus-visible:outline-2 focus-visible:outline-accent">
        {title}
      </summary>
      <div className="flex flex-col gap-3 border-t border-line px-3 py-3">{children}</div>
    </details>
  );
}

export function SmallButton({
  children,
  onClick,
  label,
  danger,
}: {
  children: ReactNode;
  onClick(): void;
  /** Accessible name when the visible text is not enough. */
  label?: string;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className={`rounded-md border border-line bg-bg px-2 py-1 text-xs hover:bg-surface focus-visible:outline-2 focus-visible:outline-accent ${danger ? "text-danger" : "text-fg"}`}
    >
      {children}
    </button>
  );
}
