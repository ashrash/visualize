import type { ImportOptions } from "../api/types";
import { Field, Input, Select } from "../components/ui/input";

const NAMES: Record<string, string> = {
  ",": "comma",
  "\t": "tab",
  ";": "semicolon",
  "|": "pipe",
};

const DELIMITERS = [
  { value: ",", label: "Comma (,)" },
  { value: "\t", label: "Tab" },
  { value: ";", label: "Semicolon (;)" },
  { value: "|", label: "Pipe (|)" },
];
// Values are Python codec names: data-core reads utf-8, utf-16 and latin-1 natively and
// transcodes the rest to UTF-8 while copying.
const ENCODINGS = [
  { value: "utf-8", label: "UTF-8" },
  { value: "utf-16", label: "UTF-16" },
  { value: "latin-1", label: "Latin-1 (ISO 8859-1)" },
  { value: "cp1252", label: "Windows-1252" },
  { value: "cp1251", label: "Windows-1251 (Cyrillic)" },
  { value: "iso8859-15", label: "Latin-9 (ISO 8859-15)" },
  { value: "shift_jis", label: "Shift JIS" },
  { value: "gb18030", label: "GB 18030 (Chinese)" },
  { value: "euc_kr", label: "EUC-KR" },
];

const auto = (detected?: string) => ({
  value: "",
  label: detected ? `Auto-detect (${detected})` : "Auto-detect",
});

/**
 * Import options. `detected` (from the live preview) is shown as the auto-detect label and as
 * placeholders, so the user sees what the reader chose before overriding it.
 */
export function ImportOptionsForm({
  options,
  detected,
  onChange,
  showSheet = true,
}: {
  options: ImportOptions;
  detected?: ImportOptions;
  onChange: (next: ImportOptions) => void;
  /** The wizard picks sheets with checkboxes instead. */
  showSheet?: boolean;
}) {
  const headerLabel =
    detected?.header === true ? "has header" : detected?.header === false ? "no header" : undefined;
  return (
    <fieldset className="grid grid-cols-1 gap-3 rounded-md border border-line p-3 sm:grid-cols-2">
      <legend className="px-1 text-xs font-medium text-muted">Options (optional)</legend>
      <Field label="Delimiter">
        {(p) => (
          <Select
            {...p}
            options={[
              auto(
                detected?.delimiter ? (NAMES[detected.delimiter] ?? detected.delimiter) : undefined,
              ),
              ...DELIMITERS,
            ]}
            value={options.delimiter ?? ""}
            onChange={(e) => onChange({ ...options, delimiter: e.target.value || null })}
          />
        )}
      </Field>
      <Field label="Encoding">
        {(p) => (
          <Select
            {...p}
            options={[auto(detected?.encoding ?? undefined), ...ENCODINGS]}
            value={options.encoding ?? ""}
            onChange={(e) => onChange({ ...options, encoding: e.target.value || null })}
          />
        )}
      </Field>
      <Field label="Header">
        {(p) => (
          <Select
            {...p}
            options={[
              auto(headerLabel),
              { value: "yes", label: "First row is a header" },
              { value: "no", label: "No header row" },
            ]}
            value={options.header === true ? "yes" : options.header === false ? "no" : ""}
            onChange={(e) =>
              onChange({
                ...options,
                header: e.target.value === "" ? null : e.target.value === "yes",
              })
            }
          />
        )}
      </Field>
      <Field label="Header row number" hint="1 = first line of the file">
        {(p) => (
          <Input
            {...p}
            type="number"
            min={1}
            placeholder={detected?.headerRow ? String(detected.headerRow) : undefined}
            value={options.headerRow ?? ""}
            onChange={(e) =>
              onChange({
                ...options,
                headerRow: e.target.value === "" ? null : Number(e.target.value),
              })
            }
          />
        )}
      </Field>
      {showSheet ? (
        <Field
          label="Excel sheet"
          hint="Excel files only; leave empty for the first sheet"
          className="sm:col-span-2"
        >
          {(p) => (
            <Input
              {...p}
              value={options.sheet ?? ""}
              onChange={(e) => onChange({ ...options, sheet: e.target.value || null })}
            />
          )}
        </Field>
      ) : null}
    </fieldset>
  );
}
