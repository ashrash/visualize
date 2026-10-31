import type { ChartSpec, ChartStyle } from "../../api/types";
import { FORMAT_PRESETS } from "../core/format";
import { PALETTES } from "../core/palettes";
import type {
  Annotation,
  AxisStyle,
  PlotEntry,
  RefLine,
  SeriesStyle,
  StyleSection,
  Trendline,
} from "../core/types";
import {
  CheckboxField,
  ColorField,
  Field,
  inputClass,
  NumberField,
  Section,
  SelectField,
  SmallButton,
  TextField,
} from "./controls";
import { patchStyle, patchXAxis, patchYAxis, yAxesOf } from "./spec";

const AUTO = "__auto__";
const CUSTOM = "__custom__";

const FONTS = [
  { value: AUTO, label: "Theme default" },
  { value: "Inter, ui-sans-serif, system-ui, sans-serif", label: "Inter" },
  {
    value: "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
    label: "System sans",
  },
  { value: "Georgia, 'Times New Roman', serif", label: "Serif" },
  { value: "ui-monospace, 'Cascadia Code', Menlo, Consolas, monospace", label: "Monospace" },
];

const LINE_STYLES = [
  { value: "solid", label: "Solid" },
  { value: "dashed", label: "Dashed" },
  { value: "dotted", label: "Dotted" },
];

const triState = (value: boolean | null | undefined) =>
  value === true ? "true" : value === false ? "false" : AUTO;
const fromTriState = (value: string): boolean | null =>
  value === "true" ? true : value === "false" ? false : null;

const TRI_OPTIONS = [
  { value: AUTO, label: "Automatic" },
  { value: "true", label: "On" },
  { value: "false", label: "Off" },
];

/** Number and date formats: a preset list, or a custom format string. */
function FormatField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string | null | undefined;
  onChange(value: string | null): void;
}) {
  const known = FORMAT_PRESETS.some((p) => p.value === value);
  const current = !value ? AUTO : known ? value : CUSTOM;
  return (
    <div className="flex flex-col gap-2">
      <Field label={label}>
        {(id) => (
          <select
            id={id}
            className={inputClass}
            value={current}
            onChange={(e) =>
              onChange(
                e.target.value === AUTO
                  ? null
                  : e.target.value === CUSTOM
                    ? (value ?? "number:2")
                    : e.target.value,
              )
            }
          >
            <option value={AUTO}>Automatic</option>
            <optgroup label="Numbers">
              {FORMAT_PRESETS.filter((p) => p.group === "number" && p.value !== "auto").map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </optgroup>
            <optgroup label="Dates">
              {FORMAT_PRESETS.filter((p) => p.group === "date").map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </optgroup>
            <option value={CUSTOM}>Custom…</option>
          </select>
        )}
      </Field>
      {current === CUSTOM ? (
        <TextField
          label={`${label}: custom format`}
          value={value}
          onChange={(v) => onChange(v || null)}
          hint="For example number:2, compact:1, percent:1, currency:EUR:0, date, month."
        />
      ) : null}
    </div>
  );
}

const SCALES = [
  { value: AUTO, label: "Automatic" },
  { value: "linear", label: "Linear" },
  { value: "log", label: "Logarithmic" },
  { value: "time", label: "Time" },
  { value: "category", label: "Category" },
];

const bound = (text: string): number | string | null => {
  if (text.trim() === "") return null;
  const n = Number(text);
  return Number.isFinite(n) ? n : text;
};

function AxisEditor({
  label,
  axis,
  onChange,
  yAxis,
}: {
  label: string;
  axis: AxisStyle;
  onChange(patch: Partial<AxisStyle>): void;
  yAxis: boolean;
}) {
  return (
    <fieldset className="flex flex-col gap-3 rounded-md border border-line p-2">
      <legend className="px-1 text-xs font-medium text-muted">{label}</legend>
      <TextField
        label={`${label}: title`}
        value={axis.title}
        onChange={(title) => onChange({ title: title || null })}
        placeholder="Automatic"
      />
      <SelectField
        label={`${label}: scale`}
        value={axis.scale ?? AUTO}
        options={SCALES}
        onChange={(v) => onChange({ scale: v === AUTO ? null : (v as AxisStyle["scale"]) })}
      />
      {axis.scale === "log" ? (
        <NumberField
          label={`${label}: log base`}
          value={axis.logBase ?? 10}
          min={2}
          onChange={(n) => onChange({ logBase: n })}
        />
      ) : null}
      <FormatField
        label={`${label}: format`}
        value={axis.format}
        onChange={(format) => onChange({ format })}
      />
      <div className="grid grid-cols-2 gap-2">
        <TextField
          label={`${label}: minimum`}
          value={axis.min === null || axis.min === undefined ? "" : String(axis.min)}
          onChange={(t) => onChange({ min: bound(t) })}
          placeholder="Auto"
        />
        <TextField
          label={`${label}: maximum`}
          value={axis.max === null || axis.max === undefined ? "" : String(axis.max)}
          onChange={(t) => onChange({ max: bound(t) })}
          placeholder="Auto"
        />
      </div>
      <NumberField
        label={`${label}: label rotation (degrees)`}
        value={axis.tickRotate}
        min={-90}
        max={90}
        onChange={(n) => onChange({ tickRotate: n })}
      />
      <SelectField
        label={`${label}: position`}
        value={axis.position ?? AUTO}
        options={[
          { value: AUTO, label: "Automatic" },
          { value: "start", label: yAxis ? "Left / bottom" : "Bottom / left" },
          { value: "end", label: yAxis ? "Right / top" : "Top / right" },
        ]}
        onChange={(v) => onChange({ position: v === AUTO ? null : (v as "start" | "end") })}
      />
      <CheckboxField
        label="Show gridlines"
        checked={axis.grid !== false}
        onChange={(grid) => onChange({ grid })}
      />
      <CheckboxField
        label="Reverse direction"
        checked={axis.inverse === true}
        onChange={(inverse) => onChange({ inverse: inverse || null })}
      />
    </fieldset>
  );
}

function yFieldNames(spec: ChartSpec): string[] {
  const list = spec.encodings?.y ?? [];
  if (list.length === 0) return ["Series 1"];
  return list.map((e) => e.label ?? e.field ?? "Row count");
}

function SeriesEditor({
  spec,
  index,
  name,
  axisCount,
  showAxis,
  onChange,
}: {
  spec: ChartSpec;
  index: number;
  name: string;
  axisCount: number;
  showAxis: boolean;
  onChange(spec: ChartSpec): void;
}) {
  const all = spec.style?.series ?? [];
  const current: SeriesStyle = all.find((s) => s.index === index) ?? { index, yAxis: 0 };
  const patch = (p: Partial<SeriesStyle>) => {
    const next = { ...current, ...p };
    const rest = all.filter((s) => s.index !== index);
    onChange(patchStyle(spec, { series: [...rest, next].sort((a, b) => a.index - b.index) }));
  };
  const label = `Series ${index + 1} (${name})`;
  return (
    <fieldset className="flex flex-col gap-3 rounded-md border border-line p-2">
      <legend className="px-1 text-xs font-medium text-muted">{label}</legend>
      {showAxis && axisCount > 1 ? (
        <SelectField
          label={`${label}: y axis`}
          value={String(current.yAxis ?? 0)}
          options={Array.from({ length: axisCount }, (_, i) => ({
            value: String(i),
            label: `Y axis ${i + 1}`,
          }))}
          onChange={(v) => patch({ yAxis: Number(v) })}
        />
      ) : null}
      <SelectField
        label={`${label}: draw as`}
        value={current.kind ?? AUTO}
        options={[
          { value: AUTO, label: "Chart default" },
          { value: "bar", label: "Bars" },
          { value: "line", label: "Line" },
          { value: "area", label: "Area" },
          { value: "scatter", label: "Points" },
        ]}
        onChange={(v) => patch({ kind: v === AUTO ? null : (v as SeriesStyle["kind"]) })}
      />
      <ColorField
        label={`${label}: color`}
        value={current.color}
        onChange={(color) => patch({ color })}
      />
      <SelectField
        label={`${label}: markers`}
        value={triState(current.marker)}
        options={TRI_OPTIONS}
        onChange={(v) => patch({ marker: fromTriState(v) })}
      />
      <NumberField
        label={`${label}: line width`}
        value={current.lineWidth}
        min={0.5}
        max={12}
        step={0.5}
        onChange={(n) => patch({ lineWidth: n })}
      />
      <SelectField
        label={`${label}: line style`}
        value={current.lineStyle ?? AUTO}
        options={[{ value: AUTO, label: "Automatic" }, ...LINE_STYLES]}
        onChange={(v) => patch({ lineStyle: v === AUTO ? null : (v as SeriesStyle["lineStyle"]) })}
      />
    </fieldset>
  );
}

function ListEditor<T>({
  items,
  add,
  addLabel,
  render,
  onChange,
  empty,
}: {
  items: T[];
  add: T;
  addLabel: string;
  render(item: T, patch: (p: Partial<T>) => void, index: number): React.ReactNode;
  onChange(items: T[]): void;
  empty: string;
}) {
  return (
    <div className="flex flex-col gap-3">
      {items.length === 0 ? <p className="text-xs text-muted">{empty}</p> : null}
      {items.map((item, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: list items have no identity beyond position
        <fieldset key={i} className="flex flex-col gap-2 rounded-md border border-line p-2">
          <legend className="px-1 text-xs font-medium text-muted">{`${addLabel} ${i + 1}`}</legend>
          {render(item, (p) => onChange(items.map((x, k) => (k === i ? { ...x, ...p } : x))), i)}
          <div>
            <SmallButton
              danger
              label={`Remove ${addLabel.toLowerCase()} ${i + 1}`}
              onClick={() => onChange(items.filter((_, k) => k !== i))}
            >
              Remove
            </SmallButton>
          </div>
        </fieldset>
      ))}
      <div>
        <SmallButton
          onClick={() => onChange([...items, add])}
        >{`+ Add ${addLabel.toLowerCase()}`}</SmallButton>
      </div>
    </div>
  );
}

const valueText = (v: number | string | null | undefined) =>
  v === null || v === undefined ? "" : String(v);

const TRENDLINES = [
  { value: "linear", label: "Linear" },
  { value: "polynomial", label: "Polynomial" },
  { value: "exponential", label: "Exponential" },
  { value: "logarithmic", label: "Logarithmic" },
  { value: "power", label: "Power" },
  { value: "loess", label: "LOESS (smoothed)" },
  { value: "moving_average", label: "Moving average" },
];

/** The Style tab: only the sections the plot declares in `styleSections`. */
export function StyleTab({
  spec,
  entry,
  onChange,
}: {
  spec: ChartSpec;
  entry: PlotEntry;
  onChange(spec: ChartSpec): void;
}) {
  const style: ChartStyle = {
    orientation: "vertical",
    stack: "none",
    showLabels: false,
    titlePosition: "top",
    ...spec.style,
  };
  const has = (s: StyleSection) => entry.styleSections.includes(s);
  const patch = (p: Partial<ChartStyle>) => onChange(patchStyle(spec, p));
  const yAxes = yAxesOf(spec);
  const ySeries = yFieldNames(spec);
  const multi = has("multiAxis");

  return (
    <div className="flex flex-col gap-2">
      {has("title") ? (
        <Section title="Title and text" defaultOpen>
          <TextField
            label="Title"
            value={style.title}
            onChange={(title) => patch({ title: title || null })}
          />
          <TextField
            label="Subtitle"
            value={style.subtitle}
            onChange={(subtitle) => patch({ subtitle: subtitle || null })}
          />
          <SelectField
            label="Title position"
            value={style.titlePosition ?? "top"}
            options={[
              { value: "top", label: "Top" },
              { value: "bottom", label: "Bottom" },
            ]}
            onChange={(titlePosition) =>
              patch({ titlePosition: titlePosition as ChartStyle["titlePosition"] })
            }
          />
          <SelectField
            label="Font"
            value={
              FONTS.some((f) => f.value === style.fontFamily) ? (style.fontFamily as string) : AUTO
            }
            options={FONTS}
            onChange={(v) => patch({ fontFamily: v === AUTO ? null : v })}
          />
          <NumberField
            label="Font size (px)"
            value={style.fontSize}
            min={8}
            max={32}
            onChange={(n) => patch({ fontSize: n })}
            placeholder="Automatic"
          />
        </Section>
      ) : null}

      {has("legend") ? (
        <Section title="Legend">
          <CheckboxField
            label="Show legend"
            checked={style.legend?.show !== false}
            onChange={(show) => patch({ legend: { position: "top", ...style.legend, show } })}
          />
          <SelectField
            label="Legend position"
            value={style.legend?.position ?? "top"}
            options={[
              { value: "top", label: "Top" },
              { value: "bottom", label: "Bottom" },
              { value: "left", label: "Left" },
              { value: "right", label: "Right" },
            ]}
            onChange={(position) =>
              patch({ legend: { show: true, ...style.legend, position: position as "top" } })
            }
          />
        </Section>
      ) : null}

      {has("axes") ? (
        <Section title="Axes">
          <AxisEditor
            label="X axis"
            yAxis={false}
            axis={{ grid: true, ...style.xAxis }}
            onChange={(p) => onChange(patchXAxis(spec, p))}
          />
          {(multi ? yAxes : yAxes.slice(0, 1)).map((axis, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: axes are identified by position
            <div key={`y${i}`} className="flex flex-col gap-2">
              <AxisEditor
                label={yAxes.length > 1 ? `Y axis ${i + 1}` : "Y axis"}
                yAxis
                axis={axis}
                onChange={(p) => onChange(patchYAxis(spec, i, p))}
              />
              {multi && yAxes.length > 1 && i > 0 ? (
                <div>
                  <SmallButton
                    danger
                    label={`Remove y axis ${i + 1}`}
                    onClick={() =>
                      patch({
                        yAxes: yAxes.filter((_, k) => k !== i),
                        series: (style.series ?? []).map((s) =>
                          s.yAxis === i
                            ? { ...s, yAxis: 0 }
                            : s.yAxis > i
                              ? { ...s, yAxis: s.yAxis - 1 }
                              : s,
                        ),
                      })
                    }
                  >
                    Remove y axis {i + 1}
                  </SmallButton>
                </div>
              ) : null}
            </div>
          ))}
          {multi ? (
            <div>
              <SmallButton onClick={() => patch({ yAxes: [...yAxes, { grid: false }] })}>
                + Add y axis
              </SmallButton>
            </div>
          ) : null}
        </Section>
      ) : null}

      {has("orientation") || has("stack") ? (
        <Section title="Layout">
          {has("orientation") ? (
            <SelectField
              label="Orientation"
              value={style.orientation}
              options={[
                { value: "vertical", label: "Vertical" },
                { value: "horizontal", label: "Horizontal" },
              ]}
              onChange={(v) => patch({ orientation: v as ChartStyle["orientation"] })}
            />
          ) : null}
          {has("stack") ? (
            <SelectField
              label="Stacking"
              value={style.stack}
              options={[
                { value: "none", label: "Side by side" },
                { value: "stack", label: "Stacked" },
                { value: "percent", label: "Stacked to 100%" },
              ]}
              onChange={(v) => patch({ stack: v as ChartStyle["stack"] })}
            />
          ) : null}
        </Section>
      ) : null}

      {has("labels") || has("numberFormat") || has("tooltip") ? (
        <Section title="Labels, numbers and tooltips">
          {has("labels") ? (
            <>
              <CheckboxField
                label="Show value labels"
                checked={style.showLabels}
                onChange={(showLabels) => patch({ showLabels })}
              />
              <SelectField
                label="Label position"
                value={style.labelPosition ?? "auto"}
                options={[
                  { value: "auto", label: "Automatic" },
                  { value: "inside", label: "Inside" },
                  { value: "outside", label: "Outside" },
                  { value: "top", label: "Top" },
                  { value: "end", label: "End" },
                ]}
                onChange={(v) => patch({ labelPosition: v as ChartStyle["labelPosition"] })}
              />
            </>
          ) : null}
          {has("numberFormat") ? (
            <FormatField
              label="Number format"
              value={style.numberFormat}
              onChange={(numberFormat) => patch({ numberFormat })}
            />
          ) : null}
          {has("tooltip") ? (
            <>
              <CheckboxField
                label="Show tooltips"
                checked={style.tooltip?.show !== false}
                onChange={(show) => patch({ tooltip: { ...style.tooltip, show } })}
              />
              <SelectField
                label="Tooltip shows"
                value={style.tooltip?.trigger ?? AUTO}
                options={[
                  { value: AUTO, label: "Chart default" },
                  { value: "axis", label: "All series at the pointer" },
                  { value: "item", label: "Only the hovered item" },
                ]}
                onChange={(v) =>
                  patch({
                    tooltip: {
                      show: true,
                      ...style.tooltip,
                      trigger: v === AUTO ? null : (v as "axis" | "item"),
                    },
                  })
                }
              />
              <FormatField
                label="Tooltip value format"
                value={style.tooltip?.format}
                onChange={(format) => patch({ tooltip: { show: true, ...style.tooltip, format } })}
              />
            </>
          ) : null}
        </Section>
      ) : null}

      {has("palette") ? (
        <Section title="Colors">
          <Field label="Palette">
            {(id) => (
              <select
                id={id}
                className={inputClass}
                value={style.palette ?? "default"}
                onChange={(e) =>
                  patch({ palette: e.target.value === "default" ? null : e.target.value })
                }
              >
                {PALETTES.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <div className="flex gap-1" aria-hidden="true">
            {(PALETTES.find((p) => p.id === (style.palette ?? "default"))?.colors ?? []).map(
              (c) => (
                <span
                  key={c}
                  className="size-4 rounded-sm border border-line"
                  style={{ background: c }}
                />
              ),
            )}
          </div>
        </Section>
      ) : null}

      {has("markers") || has("lines") ? (
        <Section title="Markers and lines">
          {has("markers") ? (
            <SelectField
              label="Markers"
              value={triState(style.markers)}
              options={TRI_OPTIONS}
              onChange={(v) => patch({ markers: fromTriState(v) })}
            />
          ) : null}
          {has("lines") ? (
            <>
              <NumberField
                label="Line width"
                value={style.lineWidth}
                min={0.5}
                max={12}
                step={0.5}
                onChange={(n) => patch({ lineWidth: n })}
                placeholder="Automatic"
              />
              <SelectField
                label="Line style"
                value={style.lineStyle ?? AUTO}
                options={[{ value: AUTO, label: "Automatic" }, ...LINE_STYLES]}
                onChange={(v) =>
                  patch({ lineStyle: v === AUTO ? null : (v as ChartStyle["lineStyle"]) })
                }
              />
              <SelectField
                label="Smooth lines"
                value={triState(style.smooth)}
                options={TRI_OPTIONS}
                onChange={(v) => patch({ smooth: fromTriState(v) })}
              />
              <NumberField
                label="Area opacity"
                value={style.areaOpacity}
                min={0.05}
                max={1}
                step={0.05}
                onChange={(n) => patch({ areaOpacity: n })}
                placeholder="Automatic"
              />
            </>
          ) : null}
        </Section>
      ) : null}

      {has("seriesStyle") ? (
        <Section title="Series">
          {ySeries.map((name, i) => (
            <SeriesEditor
              // biome-ignore lint/suspicious/noArrayIndexKey: series are identified by position
              key={`${i}-${name}`}
              spec={spec}
              index={i}
              name={name}
              axisCount={yAxes.length}
              showAxis={multi}
              onChange={onChange}
            />
          ))}
        </Section>
      ) : null}

      {has("refLines") ? (
        <Section title="Reference lines">
          <ListEditor<RefLine>
            items={style.refLines ?? []}
            add={{ axis: "y", value: 0, lineStyle: "dashed" }}
            addLabel="Reference line"
            empty="Draw a line at a value, such as a target or threshold."
            onChange={(refLines) => patch({ refLines })}
            render={(r, p, i) => (
              <>
                <SelectField
                  label={`Reference line ${i + 1}: axis`}
                  value={r.axis}
                  options={[
                    { value: "y", label: "Value axis (horizontal line)" },
                    { value: "x", label: "X axis (vertical line)" },
                  ]}
                  onChange={(axis) => p({ axis: axis as "x" | "y" })}
                />
                <TextField
                  label={`Reference line ${i + 1}: at`}
                  value={valueText(r.value)}
                  onChange={(t) => p({ value: bound(t) ?? 0 })}
                  hint="A number, a category, or a date such as 2026-01-31."
                />
                <TextField
                  label={`Reference line ${i + 1}: label`}
                  value={r.label}
                  onChange={(label) => p({ label: label || null })}
                />
                <SelectField
                  label={`Reference line ${i + 1}: style`}
                  value={r.lineStyle ?? "dashed"}
                  options={LINE_STYLES}
                  onChange={(lineStyle) => p({ lineStyle: lineStyle as RefLine["lineStyle"] })}
                />
              </>
            )}
          />
        </Section>
      ) : null}

      {has("annotations") ? (
        <Section title="Annotations">
          <ListEditor<Annotation>
            items={style.annotations ?? []}
            add={{ kind: "point", text: "Note" }}
            addLabel="Annotation"
            empty="Mark a point, shade a region, or place text on the chart."
            onChange={(annotations) => patch({ annotations })}
            render={(a, p, i) => (
              <>
                <SelectField
                  label={`Annotation ${i + 1}: type`}
                  value={a.kind}
                  options={[
                    { value: "point", label: "Point marker" },
                    { value: "area", label: "Shaded region" },
                    { value: "text", label: "Text" },
                  ]}
                  onChange={(kind) => p({ kind: kind as Annotation["kind"] })}
                />
                <div className="grid grid-cols-2 gap-2">
                  <TextField
                    label={`Annotation ${i + 1}: x`}
                    value={valueText(a.x)}
                    onChange={(t) => p({ x: bound(t) })}
                  />
                  <TextField
                    label={`Annotation ${i + 1}: y`}
                    value={valueText(a.y)}
                    onChange={(t) => p({ y: bound(t) })}
                  />
                  {a.kind === "area" ? (
                    <>
                      <TextField
                        label={`Annotation ${i + 1}: x to`}
                        value={valueText(a.x2)}
                        onChange={(t) => p({ x2: bound(t) })}
                      />
                      <TextField
                        label={`Annotation ${i + 1}: y to`}
                        value={valueText(a.y2)}
                        onChange={(t) => p({ y2: bound(t) })}
                      />
                    </>
                  ) : null}
                </div>
                <TextField
                  label={`Annotation ${i + 1}: text`}
                  value={a.text}
                  onChange={(text) => p({ text: text || null })}
                />
              </>
            )}
          />
        </Section>
      ) : null}

      {has("trendline") ? (
        <Section title="Trendlines and confidence bands">
          <p className="text-xs text-muted">
            Fitted on the server from all rows, so they stay exact on large data.
          </p>
          <ListEditor<Trendline>
            items={style.trendlines ?? []}
            add={{ kind: "linear", series: 0, degree: 2, showEquation: false, showR2: false }}
            addLabel="Trendline"
            empty="Add a fitted line to one series."
            onChange={(trendlines) => patch({ trendlines })}
            render={(t, p, i) => (
              <>
                <SelectField
                  label={`Trendline ${i + 1}: series`}
                  value={String(t.series ?? 0)}
                  options={ySeries.map((n, k) => ({ value: String(k), label: `${k + 1}. ${n}` }))}
                  onChange={(v) => p({ series: Number(v) })}
                />
                <SelectField
                  label={`Trendline ${i + 1}: type`}
                  value={t.kind ?? "linear"}
                  options={TRENDLINES}
                  onChange={(kind) => p({ kind: kind as Trendline["kind"] })}
                />
                {t.kind === "polynomial" ? (
                  <NumberField
                    label={`Trendline ${i + 1}: degree`}
                    value={t.degree}
                    min={2}
                    max={8}
                    onChange={(n) => p({ degree: n ?? 2 })}
                  />
                ) : null}
                {t.kind === "moving_average" ? (
                  <NumberField
                    label={`Trendline ${i + 1}: window (points)`}
                    value={t.window}
                    min={2}
                    onChange={(n) => p({ window: n })}
                  />
                ) : null}
                <NumberField
                  label={`Trendline ${i + 1}: confidence band level (0-1, empty = none)`}
                  value={t.ciLevel}
                  min={0.5}
                  max={0.999}
                  step={0.01}
                  onChange={(n) => p({ ciLevel: n })}
                />
                <CheckboxField
                  label="Show equation"
                  checked={t.showEquation === true}
                  onChange={(showEquation) => p({ showEquation })}
                />
                <CheckboxField
                  label="Show R squared"
                  checked={t.showR2 === true}
                  onChange={(showR2) => p({ showR2 })}
                />
              </>
            )}
          />
        </Section>
      ) : null}

      {has("errorBars") ? (
        <Section title="Error bars">
          <SelectField
            label="Error bars"
            value={style.errorBars?.kind ?? AUTO}
            options={[
              { value: AUTO, label: "None" },
              { value: "sd", label: "Standard deviation" },
              { value: "se", label: "Standard error" },
              { value: "ci", label: "Confidence interval" },
              { value: "fixed", label: "Fixed amount" },
            ]}
            onChange={(v) =>
              patch({
                errorBars: v === AUTO ? null : { level: 0.95, ...style.errorBars, kind: v as "sd" },
              })
            }
          />
          {style.errorBars?.kind === "ci" ? (
            <NumberField
              label="Confidence level"
              value={style.errorBars.level}
              min={0.5}
              max={0.999}
              step={0.01}
              onChange={(n) =>
                patch({ errorBars: { ...style.errorBars, kind: "ci", level: n ?? 0.95 } })
              }
            />
          ) : null}
          {style.errorBars?.kind === "fixed" ? (
            <NumberField
              label="Amount (plus and minus)"
              value={style.errorBars.value}
              min={0}
              onChange={(n) =>
                patch({ errorBars: { level: 0.95, ...style.errorBars, kind: "fixed", value: n } })
              }
            />
          ) : null}
        </Section>
      ) : null}
    </div>
  );
}
