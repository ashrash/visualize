/**
 * Which chart did the user actually touch?
 *
 * `echarts.connect` replays every action that has an event (brush, brushEnd, legend toggles,
 * zoom, highlight) on the other charts of a link group, and those charts emit the same events as
 * if the user had acted on them. Reporting a brush or a legend toggle to the dashboard must only
 * happen on the chart the pointer was on, otherwise a band drawn on chart A would be read against
 * chart B's own axis and filter by values nobody selected.
 *
 * The gate watches this chart's own zrender pointer events: a gesture is "local" from `mousedown`
 * until a short grace period after `mouseup`. Brush events are dispatched synchronously from the
 * mouseup handler; the grace period exists for the legend, whose `click` (and toggle) can follow
 * the `mouseup`. Touch input reaches zrender as mouse events too.
 *
 * The gate only decides what is REPORTED. Keeping a replayed brush band off the chart is done
 * separately (see `rememberBrush`).
 */
export interface PointerSource {
  on(event: string, handler: () => void): unknown;
  off(event: string, handler: () => void): unknown;
}

export interface GestureGate {
  /** True while, or just after, the user is working on this chart. */
  isLocal(): boolean;
  dispose(): void;
}

/** Longest a button can stay "down" before the gate stops trusting it (a lost mouseup). */
const HELD_MS = 30_000;

/**
 * There is one pointer, so only one chart at a time can own a gesture: the chart that last
 * received a `mousedown`. A quick click on chart B right after a brush on chart A must not let A
 * (still inside its grace period) mistake B's replayed event for its own.
 */
let owner: symbol | null = null;

export function createGestureGate(
  zr: PointerSource,
  o: { graceMs?: number; now?: () => number } = {},
): GestureGate {
  const grace = o.graceMs ?? 600;
  const now = o.now ?? Date.now;
  const me = Symbol("chart");
  let until = 0;
  const down = () => {
    owner = me;
    until = now() + HELD_MS;
  };
  const up = () => {
    owner = me;
    until = now() + grace;
  };
  zr.on("mousedown", down);
  zr.on("mouseup", up);
  return {
    isLocal: () => owner === me && now() <= until,
    dispose() {
      zr.off("mousedown", down);
      zr.off("mouseup", up);
      if (owner === me) owner = null;
    },
  };
}

/** One brush area as ECharts reports it (and accepts it back in a `brush` action). */
export type BrushArea = Record<string, unknown>;

// The band the user drew on each chart themselves. `echarts.connect` replays other charts' brush
// actions onto a chart and ECharts would draw that band against the wrong axis; the chart puts
// its own band back (with `escapeConnect`, so the restore is not replayed in turn).
const own = new WeakMap<object, BrushArea[]>();

export function rememberBrush(chart: object, areas: BrushArea[]): void {
  own.set(chart, areas);
}

export function ownBrush(chart: object): BrushArea[] {
  return own.get(chart) ?? [];
}

/** Two area lists describe the same bands (compared by shape and range, not pixel details). */
export function sameBrush(a: BrushArea[], b: BrushArea[]): boolean {
  const key = (areas: BrushArea[]) =>
    JSON.stringify(areas.map((x) => [x.brushType, x.coordRange ?? x.range]));
  return key(a) === key(b);
}
