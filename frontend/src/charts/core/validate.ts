import type { JsonValue, ParamSchema, PlotEntry } from "./types";

function matchesParam(schema: ParamSchema, value: JsonValue | undefined): boolean {
  if (value === undefined) return true;
  switch (schema.type) {
    case "number":
    case "int":
      return (
        typeof value === "number" &&
        (schema.min === undefined || value >= schema.min) &&
        (schema.max === undefined || value <= schema.max)
      );
    case "boolean":
      return typeof value === "boolean";
    case "select":
      return typeof value === "string" && schema.options.some((o) => o.value === value);
    case "text":
    case "color":
    case "column":
      return typeof value === "string";
    case "range":
      return (
        Array.isArray(value) && value.length === 2 && value.every((v) => typeof v === "number")
      );
  }
}

/**
 * Problems with a registry entry, as readable strings (empty = valid): unique channel and param
 * names, `showIf` references that resolve, defaults that match their schemas. Run on every
 * registered plot by the registry test, so a plot agent finds out in `npm test`.
 */
export function validateEntry(entry: PlotEntry): string[] {
  const problems: string[] = [];
  const where = `${entry.key}:`;
  const channelNames = entry.encodingsSchema.map((c) => c.name);
  const paramNames = entry.paramsSchema.map((p) => p.name);

  for (const names of [channelNames, paramNames]) {
    const dupes = names.filter((n, i) => names.indexOf(n) !== i);
    if (dupes.length) problems.push(`${where} duplicate names ${[...new Set(dupes)].join(", ")}`);
  }
  for (const name of paramNames) {
    if (channelNames.includes(name))
      problems.push(`${where} "${name}" is both a channel and a param`);
  }
  if (entry.encodingsSchema.length === 0) {
    problems.push(`${where} declares no channels`);
  }
  for (const param of entry.paramsSchema) {
    // Required by the type, but plot modules can be loose (casts, JS): check at runtime too, so a
    // `bins` param that never re-queries cannot ship.
    if (typeof (param as { affectsData?: unknown }).affectsData !== "boolean") {
      problems.push(
        `${where} param "${param.name}" must set affectsData (true if the server needs it for the chart data, else false)`,
      );
    }
    const conds = param.showIf ? (Array.isArray(param.showIf) ? param.showIf : [param.showIf]) : [];
    for (const { param: ref } of conds) {
      if (!paramNames.includes(ref) && !channelNames.includes(ref)) {
        problems.push(`${where} param "${param.name}" showIf references unknown "${ref}"`);
      }
    }
    if ("default" in param && !matchesParam(param, param.default as JsonValue)) {
      problems.push(`${where} param "${param.name}" default does not match its schema`);
    }
  }
  for (const [name, value] of Object.entries(entry.defaults.params ?? {})) {
    const schema = entry.paramsSchema.find((p) => p.name === name);
    if (!schema) problems.push(`${where} defaults.params has unknown param "${name}"`);
    else if (!matchesParam(schema, value)) {
      problems.push(`${where} defaults.params "${name}" does not match its schema`);
    }
  }
  for (const channel of Object.keys(entry.defaults.encodings ?? {})) {
    if (!channelNames.includes(channel)) {
      problems.push(`${where} defaults.encodings has unknown channel "${channel}"`);
    }
  }
  if (entry.drill) {
    const channel = entry.encodingsSchema.find((c) => c.name === entry.drill?.channel);
    if (!channel) problems.push(`${where} drill.channel "${entry.drill.channel}" is not a channel`);
    else if (channel.multiple) problems.push(`${where} drill.channel must be a single channel`);
  }
  if (entry.styleSections.length === 0) problems.push(`${where} styleSections is empty`);
  return problems;
}
