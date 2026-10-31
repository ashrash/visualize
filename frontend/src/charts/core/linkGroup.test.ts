import type { ECharts } from "echarts/core";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { connect, disconnect } = vi.hoisted(() => ({ connect: vi.fn(), disconnect: vi.fn() }));
vi.mock("./echarts", () => ({ echarts: { connect, disconnect } }));

import { joinLinkGroup, linkGroupSize } from "./linkGroup";

const fakeChart = () => ({ group: "" }) as unknown as ECharts;

beforeEach(() => {
  connect.mockClear();
  disconnect.mockClear();
});

describe("joinLinkGroup", () => {
  it("puts the chart in the group and connects it", () => {
    const chart = fakeChart();
    const leave = joinLinkGroup(chart, "g1");
    expect(chart.group).toBe("g1");
    expect(connect).toHaveBeenCalledWith("g1");
    expect(linkGroupSize("g1")).toBe(1);
    leave();
  });

  it("keeps the group connected until its last chart leaves", () => {
    const a = fakeChart();
    const b = fakeChart();
    const leaveA = joinLinkGroup(a, "g2");
    const leaveB = joinLinkGroup(b, "g2");
    expect(linkGroupSize("g2")).toBe(2);
    leaveA();
    expect(a.group).toBe("");
    expect(disconnect).not.toHaveBeenCalled();
    leaveB();
    expect(disconnect).toHaveBeenCalledWith("g2");
    expect(linkGroupSize("g2")).toBe(0);
  });

  it("is safe to leave twice, and groups are independent", () => {
    const a = fakeChart();
    const b = fakeChart();
    const leaveA = joinLinkGroup(a, "g3");
    const leaveB = joinLinkGroup(b, "g4");
    leaveA();
    leaveA();
    expect(disconnect).toHaveBeenCalledTimes(1);
    expect(linkGroupSize("g4")).toBe(1);
    leaveB();
  });
});
