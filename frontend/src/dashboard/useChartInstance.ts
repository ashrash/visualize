import { type ECharts, getInstanceByDom } from "echarts/core";
import { type RefObject, useEffect, useState } from "react";

/**
 * The ECharts instance rendered somewhere inside `frame`, tracked as it is created, rebuilt or
 * disposed (the chart mounts after its data arrives).
 *
 * TODO(ui-charts): replaced by `ChartView` callbacks once they exist (see chartEvents.ts).
 */
export function useChartInstance(frame: RefObject<HTMLElement | null>): ECharts | null {
  const [chart, setChart] = useState<ECharts | null>(null);
  useEffect(() => {
    const root = frame.current;
    if (!root) return;
    const find = () => {
      const el = root.querySelector<HTMLElement>("[_echarts_instance_]");
      const found = el ? (getInstanceByDom(el) ?? null) : null;
      setChart((previous) => (previous === found ? previous : found));
    };
    find();
    const observer = new MutationObserver(find);
    observer.observe(root, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [frame]);
  return chart;
}
