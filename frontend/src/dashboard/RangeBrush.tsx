import type { ECharts } from "echarts/core";
import { MoveHorizontal } from "lucide-react";
import { type RefObject, useEffect, useRef, useState } from "react";
import { cn } from "../components/ui/cn";
import { brushSpanFromPixels } from "./chartEvents";
import type { BrushSpan } from "./predicates";

/** The toggle that turns range selection on for a chart. */
export function RangeBrushToggle({ on, onToggle }: { on: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onToggle}
      className={cn(
        "inline-flex items-center gap-1 rounded-md border border-line px-2 py-0.5 text-xs text-fg hover:bg-surface focus-visible:outline-2 focus-visible:outline-accent",
        on ? "bg-surface-2" : "bg-bg",
      )}
    >
      <MoveHorizontal size={12} aria-hidden="true" />
      Select range
    </button>
  );
}

interface LayerProps {
  chart: ECharts | null;
  /** The positioned element the layer lives in. */
  frame: RefObject<HTMLElement | null>;
  /** Called while dragging (debounced by the caller) and when the drag ends. */
  onSpan: (span: BrushSpan | null) => void;
  /** True while a selection from this chart is in effect (keeps the drawn range visible). */
  selected: boolean;
}

/**
 * A transparent layer exactly over the chart that turns a horizontal drag into a span. It is
 * drawn here rather than with ECharts' own brush tool, which gave no events when a drag started
 * on a bar (checked in a real browser); only `convertFromPixel` is needed from the chart.
 * Keyboard users get the same filtering from clicking bars (shift-click for several).
 */
export function RangeBrushLayer({ chart, frame, onSpan, selected }: LayerProps) {
  const [drag, setDrag] = useState<{ from: number; to: number } | null>(null);
  const [shown, setShown] = useState<{ from: number; to: number } | null>(null);
  const layer = useRef<HTMLDivElement>(null);
  // Re-measure when the frame resizes (the layer's box is read at render time).
  const [, setSize] = useState(0);
  useEffect(() => {
    const el = frame.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setSize((n) => n + 1));
    observer.observe(el);
    return () => observer.disconnect();
  }, [frame]);

  const dom = chart?.getDom();
  const outer = frame.current?.getBoundingClientRect();
  const inner = dom?.getBoundingClientRect();
  if (!chart || !outer || !inner) return null;
  const box = {
    left: inner.left - outer.left,
    top: inner.top - outer.top,
    width: inner.width,
    height: inner.height,
  };

  const local = (clientX: number) => {
    const rect = layer.current?.getBoundingClientRect();
    return rect ? Math.min(Math.max(clientX - rect.left, 0), rect.width) : 0;
  };
  const emit = (from: number, to: number) => onSpan(brushSpanFromPixels(chart, from, to));
  // The drawn range goes away when the selection does (cleared elsewhere).
  const visible = drag ?? (selected ? shown : null);

  return (
    <div
      ref={layer}
      data-testid="range-brush"
      className="absolute z-10 cursor-crosshair touch-none"
      style={box}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture?.(e.pointerId);
        const x = local(e.clientX);
        setDrag({ from: x, to: x });
      }}
      onPointerMove={(e) => {
        if (!drag) return;
        const next = { from: drag.from, to: local(e.clientX) };
        setDrag(next);
        if (Math.abs(next.to - next.from) >= 3) emit(next.from, next.to);
      }}
      onPointerCancel={() => setDrag(null)}
      onPointerUp={(e) => {
        if (!drag) return;
        const to = local(e.clientX);
        setDrag(null);
        // A click without a drag is not a range.
        if (Math.abs(to - drag.from) < 3) return;
        setShown({ from: drag.from, to });
        emit(drag.from, to);
      }}
    >
      {visible ? (
        <div
          className="absolute top-0 bottom-0 border-x border-accent bg-accent/15"
          style={{
            left: Math.min(visible.from, visible.to),
            width: Math.abs(visible.to - visible.from),
          }}
        />
      ) : null}
    </div>
  );
}
