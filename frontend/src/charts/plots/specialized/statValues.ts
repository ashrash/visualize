// `StatResult.values` is typed `{[key: string]: unknown}` in the generated schema (the backend's
// JsonValue is opaque to OpenAPI), so these parsers narrow plain `unknown` -- not the stricter
// `JsonValue` type `core/types.ts` uses elsewhere -- without ever reaching for `any`.
type Unrecord = Record<string, unknown>;

function isRecord(v: unknown): v is Unrecord {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function isArray(v: unknown): v is unknown[] {
  return Array.isArray(v);
}

function num(v: unknown, fallback = 0): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function numOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function str(v: unknown): string {
  return typeof v === "string" ? v : v === null || v === undefined ? "" : JSON.stringify(v);
}

function numArray(v: unknown): number[] {
  return isArray(v) ? v.filter((x): x is number => typeof x === "number") : [];
}

// ---------------------------------------------------------------- graphs

export interface GraphNode {
  name: string;
  value: number;
  label?: string;
  depth?: number;
}

export interface GraphLink {
  source: string;
  target: string;
  value: number;
}

export function graphNodes(values: Unrecord | undefined): GraphNode[] {
  const raw = values?.nodes;
  if (!isArray(raw)) return [];
  return raw.filter(isRecord).map((n) => ({
    name: str(n.name),
    value: num(n.value),
    label: typeof n.label === "string" ? n.label : undefined,
    depth: typeof n.depth === "number" ? n.depth : undefined,
  }));
}

export function graphLinks(values: Unrecord | undefined): GraphLink[] {
  const raw = values?.links;
  if (!isArray(raw)) return [];
  return raw.filter(isRecord).map((l) => ({
    source: str(l.source),
    target: str(l.target),
    value: num(l.value),
  }));
}

// ---------------------------------------------------------------- ridgeline / raincloud

export interface DensityGroup {
  key: string | null;
  n: number;
  x: number[];
  density: number[];
}

export function densityGroups(values: Unrecord | undefined): DensityGroup[] {
  const raw = values?.groups;
  if (!isArray(raw)) return [];
  return raw.filter(isRecord).map((g) => ({
    key: typeof g.key === "string" ? g.key : null,
    n: num(g.n),
    x: numArray(g.x),
    density: numArray(g.density),
  }));
}

export interface BoxStats {
  n: number;
  q1: number | null;
  median: number | null;
  q3: number | null;
  lo: number | null;
  hi: number | null;
}

export interface RaincloudGroup {
  key: string | null;
  n: number;
  density: { x: number[]; density: number[] };
  box: BoxStats;
  points: number[];
}

export function raincloudGroups(values: Unrecord | undefined): RaincloudGroup[] {
  const raw = values?.groups;
  if (!isArray(raw)) return [];
  return raw.filter(isRecord).map((g) => {
    const density = isRecord(g.density) ? g.density : {};
    const box = isRecord(g.box) ? g.box : {};
    return {
      key: typeof g.key === "string" ? g.key : null,
      n: num(g.n),
      density: { x: numArray(density.x), density: numArray(density.density) },
      box: {
        n: num(box.n),
        q1: numOrNull(box.q1),
        median: numOrNull(box.median),
        q3: numOrNull(box.q3),
        lo: numOrNull(box.lo),
        hi: numOrNull(box.hi),
      },
      points: numArray(g.points),
    };
  });
}
