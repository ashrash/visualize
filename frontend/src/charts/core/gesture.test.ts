// @vitest-environment node
import { describe, expect, it } from "vitest";
import { echarts } from "./echarts";
import { createGestureGate, ownBrush, rememberBrush, sameBrush } from "./gesture";

function fakeZr() {
  const handlers = new Map<string, Set<() => void>>();
  return {
    on: (e: string, h: () => void) => {
      if (!handlers.has(e)) handlers.set(e, new Set());
      handlers.get(e)?.add(h);
    },
    off: (e: string, h: () => void) => handlers.get(e)?.delete(h),
    fire: (e: string) => {
      for (const h of handlers.get(e) ?? []) h();
    },
  };
}

describe("createGestureGate", () => {
  it("is local from mousedown until a grace period after mouseup", () => {
    const zr = fakeZr();
    let t = 1000;
    const gate = createGestureGate(zr, { graceMs: 500, now: () => t });
    expect(gate.isLocal()).toBe(false);
    zr.fire("mousedown");
    t += 5000; // a long drag
    expect(gate.isLocal()).toBe(true);
    zr.fire("mouseup");
    t += 400;
    expect(gate.isLocal()).toBe(true);
    t += 200;
    expect(gate.isLocal()).toBe(false);
  });

  it("stops trusting a button that never came back up", () => {
    const zr = fakeZr();
    let t = 0;
    const gate = createGestureGate(zr, { now: () => t });
    zr.fire("mousedown");
    t += 31_000;
    expect(gate.isLocal()).toBe(false);
  });

  it("lets only the chart that last got the pointer own the gesture", () => {
    const a = fakeZr();
    const b = fakeZr();
    let t = 0;
    const gateA = createGestureGate(a, { graceMs: 600, now: () => t });
    const gateB = createGestureGate(b, { graceMs: 600, now: () => t });
    a.fire("mousedown");
    a.fire("mouseup");
    t += 100; // still inside A's grace period...
    b.fire("mousedown"); // ...but the user is now working on B
    b.fire("mouseup");
    expect(gateA.isLocal()).toBe(false);
    expect(gateB.isLocal()).toBe(true);
    gateA.dispose();
    gateB.dispose();
  });

  it("stops listening when disposed", () => {
    const zr = fakeZr();
    const t = 0;
    const gate = createGestureGate(zr, { graceMs: 100, now: () => t });
    zr.fire("mousedown");
    expect(gate.isLocal()).toBe(true);
    gate.dispose();
    zr.fire("mousedown");
    expect(gate.isLocal()).toBe(false);
  });
});

// zrender reads coordinates off the packet it is given.
const PACKET = {
  event: { zrX: 0, zrY: 0, offsetX: 0, offsetY: 0 },
  offsetX: 0,
  offsetY: 0,
  zrX: 0,
  zrY: 0,
};

describe("linked charts through real ECharts", () => {
  const make = () => {
    const chart = echarts.init(null, undefined, {
      renderer: "svg",
      ssr: true,
      width: 300,
      height: 200,
    });
    chart.setOption({
      animation: false,
      xAxis: { type: "category", data: ["a", "b", "c"] },
      yAxis: { type: "value" },
      legend: { data: ["s"] },
      brush: { xAxisIndex: 0, brushType: "lineX" },
      series: [{ type: "bar", name: "s", data: [1, 2, 3] }],
    });
    return chart;
  };

  it("replays a legend toggle on a connected chart, and the gate tells the two apart", () => {
    const a = make();
    const b = make();
    a.group = "pair";
    b.group = "pair";
    echarts.connect("pair");
    const zrA = a.getZr() as unknown as Parameters<typeof createGestureGate>[0] & {
      trigger(e: string, ev?: unknown): void;
    };
    const gateA = createGestureGate(zrA);
    const gateB = createGestureGate(
      b.getZr() as unknown as Parameters<typeof createGestureGate>[0],
    );
    const reported: string[] = [];
    const raw: string[] = [];
    for (const [name, chart, gate] of [
      ["A", a, gateA],
      ["B", b, gateB],
    ] as const) {
      chart.on("legendselectchanged", () => {
        raw.push(name);
        if (gate.isLocal()) reported.push(name);
      });
    }
    // The user presses the mouse on chart A and toggles its legend.
    zrA.trigger("mousedown", PACKET);
    a.dispatchAction({ type: "legendToggleSelect", name: "s" });
    zrA.trigger("mouseup", PACKET);
    // Control: ECharts really does emit the event on B as well (why the gate exists)...
    expect(raw).toContain("B");
    // ...but only A's counts as the user's gesture.
    expect(reported).toEqual(["A"]);
    gateA.dispose();
    gateB.dispose();
    echarts.disconnect("pair");
    a.dispose();
    b.dispose();
  });

  type Areas = Parameters<typeof rememberBrush>[1];
  /** The `brush` events a chart emits, with the areas each carried. */
  const watch = (chart: ReturnType<typeof make>) => {
    const seen: Areas[] = [];
    chart.on("brush", (e) => {
      seen.push((e as { areas: Areas }).areas);
    });
    return seen;
  };
  const bandOf = (range: number[]) => ({ brushType: "lineX", coordRange: range, xAxisIndex: 0 });

  it("escapeConnect keeps a brush dispatch from being replayed on the linked chart", () => {
    const a = make();
    const b = make();
    a.group = "bands";
    b.group = "bands";
    echarts.connect("bands");
    const onA = watch(a);
    const onB = watch(b);
    // Control: a plain dispatch reaches both charts (the replay this is about).
    a.dispatchAction({ type: "brush", areas: [bandOf([0, 1])] });
    expect(onA).toHaveLength(1);
    expect(onB).toHaveLength(1);
    // With escapeConnect, a clear on B stays on B.
    b.dispatchAction({ type: "brush", areas: [], escapeConnect: true } as never);
    expect(onB).toHaveLength(2);
    expect(onA).toHaveLength(1);
    echarts.disconnect("bands");
    a.dispose();
    b.dispose();
  });

  it("restores a chart's own band after a replay, without replaying the restore", () => {
    const a = make();
    const b = make();
    a.group = "restore";
    b.group = "restore";
    echarts.connect("restore");
    const onA = watch(a);
    const onB = watch(b);
    a.dispatchAction({ type: "brush", areas: [bandOf([0, 1])] });
    // What EChart remembers when A's own brush ends.
    rememberBrush(a, onA[0] as Areas);
    // B is brushed: the replay lands on A, carrying B's band.
    b.dispatchAction({ type: "brush", areas: [bandOf([2, 2])] });
    const replayed = onA.at(-1) as Areas;
    expect(sameBrush(replayed, ownBrush(a))).toBe(false);
    // The restore, exactly as EChart does it.
    const bEvents = onB.length;
    a.dispatchAction({ type: "brush", areas: ownBrush(a), escapeConnect: true } as never);
    expect(sameBrush(onA.at(-1) as Areas, ownBrush(a))).toBe(true);
    expect(onB).toHaveLength(bEvents);
    echarts.disconnect("restore");
    a.dispose();
    b.dispose();
  });
});
