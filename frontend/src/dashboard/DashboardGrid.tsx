import { GripVertical, Trash2 } from "lucide-react";
import { type KeyboardEvent, useId, useMemo, useRef, useState } from "react";
import { Responsive, useContainerWidth } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import { cn } from "../components/ui/cn";
import { useDashboardEnv } from "./env";
import {
  GRID_COLUMNS,
  type GridPosition,
  isArrowKey,
  type Step,
  type StepKey,
  stepLayout,
} from "./model";
import type { DashboardTab, LayoutItem } from "./types";
import { ChartWidget } from "./widgets/ChartWidget";
import { FilterWidget } from "./widgets/FilterWidget";
import { ImageWidget } from "./widgets/ImageWidget";
import { KpiWidget } from "./widgets/KpiWidget";
import { TextWidget } from "./widgets/TextWidget";

// 12 columns on desktop and tablet; fewer on narrow screens (react-grid-layout re-flows the
// 12-column layout, which is the only one stored in the document).
const BREAKPOINTS = { lg: 1100, md: 800, sm: 560, xs: 400, xxs: 0 };
const COLS = { lg: GRID_COLUMNS, md: GRID_COLUMNS, sm: 6, xs: 4, xxs: 2 };
const ROW_HEIGHT = 56;
const MARGIN: [number, number] = [12, 12];

function widgetTitle(item: LayoutItem, chartName: string | undefined): string {
  const w = item.widget;
  switch (w.kind) {
    case "chart":
      return chartName ?? "Chart";
    case "kpi":
      return w.title;
    case "text":
      return "Text";
    case "image":
      return w.alt || "Image";
    case "filter":
      return "Filter";
  }
}

function WidgetBody({ item }: { item: LayoutItem }) {
  const w = item.widget;
  switch (w.kind) {
    case "chart":
      return <ChartWidget widgetId={item.i} chartId={w.chartId} />;
    case "kpi":
      return <KpiWidget widgetId={item.i} widget={w} />;
    case "text":
      return <TextWidget markdown={w.markdown} />;
    case "image":
      return <ImageWidget widget={w} />;
    case "filter":
      return <FilterWidget widget={w} />;
  }
}

function WidgetFrame({
  item,
  onRemove,
  keyboard,
}: {
  item: LayoutItem;
  onRemove?: () => void;
  /** What the keyboard needs from the grid; absent when the dashboard cannot be edited. */
  keyboard?: {
    /** The whole layout as it is now, to put back on Escape. */
    snapshot: () => GridPosition[];
    /** One step from `from`, applied; null when nothing would change. */
    step: (from: GridPosition, key: StepKey, resize: boolean) => Step | null;
    restore: (layout: GridPosition[]) => void;
  };
}) {
  const env = useDashboardEnv();
  const help = useId();
  // Keyboard move and resize: Space picks the widget up, arrows move it, Shift+arrows resize it,
  // Space or Enter puts it down, Escape puts it back where it was. The same edits as dragging.
  const [grabbed, setGrabbed] = useState(false);
  const [said, setSaid] = useState("");
  // The whole layout at pick-up, so Escape puts back the neighbours as well as this widget.
  const origin = useRef<GridPosition[] | null>(null);
  // Where the widget is while it is picked up. The item prop lags a render behind, and a key
  // pressed in between would otherwise start from the old position and undo the last step.
  const live = useRef<GridPosition | null>(null);
  const here = (): GridPosition => ({ i: item.i, x: item.x, y: item.y, w: item.w, h: item.h });
  const whereIs = (p: GridPosition) =>
    `column ${p.x + 1}, row ${p.y + 1}, ${p.w} wide, ${p.h} high`;
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      if (grabbed) {
        setGrabbed(false);
        setSaid(`Dropped at ${whereIs(live.current ?? here())}.`);
        live.current = null;
      } else {
        origin.current = keyboard?.snapshot() ?? null;
        live.current = here();
        setGrabbed(true);
        setSaid(
          "Picked up. Arrow keys move, Shift and arrow keys resize, Space drops, Escape cancels.",
        );
      }
    } else if (grabbed && event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      if (origin.current) keyboard?.restore(origin.current);
      origin.current = null;
      live.current = null;
      setGrabbed(false);
      setSaid("Cancelled. Back where it was.");
    } else if (grabbed && isArrowKey(event.key)) {
      event.preventDefault();
      const step = keyboard?.step(live.current ?? here(), event.key, event.shiftKey) ?? null;
      if (step) {
        // Follow where the grid really put it (it compacts upwards and pushes neighbours), so the
        // next key builds on that and the announcement is true.
        live.current = step.position;
        setSaid(whereIs(step.position));
      } else {
        setSaid(
          event.shiftKey
            ? "It can't be resized any further that way."
            : "It can't move any further that way.",
        );
      }
    }
  };
  const editing = env.mode === "edit";
  const chartName =
    item.widget.kind === "chart"
      ? env.charts.find((c) => c.id === (item.widget as { chartId: string }).chartId)?.name
      : undefined;
  const title = widgetTitle(item, chartName);
  return (
    <section
      aria-label={title}
      className="dashboard-widget flex h-full flex-col overflow-hidden rounded-lg border border-line bg-bg"
    >
      {editing ? (
        <header className="flex items-center gap-1 border-b border-line bg-surface px-1">
          {/* The drag handle is also the keyboard control for moving and resizing. */}
          <button
            type="button"
            aria-label={`Move or resize ${title}`}
            aria-pressed={grabbed}
            aria-describedby={help}
            onKeyDown={onKeyDown}
            onBlur={() => {
              live.current = null;
              setGrabbed(false);
            }}
            className="dash-drag-handle flex flex-1 cursor-grab items-center gap-1 rounded py-1 text-xs text-muted focus-visible:outline-2 focus-visible:outline-accent active:cursor-grabbing"
            title="Drag to move, or press Space and use the arrow keys"
          >
            <GripVertical size={14} aria-hidden="true" />
            <span className="truncate">{title}</span>
            <span id={help} className="sr-only">
              Press Space to pick up, then arrow keys to move and Shift with arrow keys to resize.
            </span>
          </button>
          <span role="status" className="sr-only">
            {said}
          </span>
          {onRemove ? (
            <button
              type="button"
              aria-label={`Remove ${title}`}
              onClick={onRemove}
              className="rounded p-1 text-muted hover:bg-surface-2 hover:text-danger focus-visible:outline-2 focus-visible:outline-accent"
            >
              <Trash2 size={14} aria-hidden="true" />
            </button>
          ) : null}
        </header>
      ) : null}
      <div className={cn("min-h-0 flex-1", item.widget.kind === "chart" && "p-1")}>
        <WidgetBody item={item} />
      </div>
    </section>
  );
}

interface Props {
  tab: DashboardTab;
  onLayout?: (layout: readonly GridPosition[]) => void;
  onRemove?: (itemId: string) => void;
}

export function DashboardGrid({ tab, onLayout, onRemove }: Props) {
  const env = useDashboardEnv();
  const editing = env.mode === "edit";
  const { width, containerRef, mounted } = useContainerWidth();
  const cols = useRef<number>(GRID_COLUMNS);

  const items = tab.items ?? [];
  const positions = (): GridPosition[] =>
    items.map((it) => ({ i: it.i, x: it.x, y: it.y, w: it.w, h: it.h }));
  const layout = useMemo(
    () => items.map(({ i, x, y, w, h }) => ({ i, x, y, w, h, minW: 2, minH: 2 })),
    [items],
  );
  const layouts = useMemo(() => ({ lg: layout, md: layout }), [layout]);

  return (
    <div ref={containerRef} className="dashboard-grid">
      {mounted ? (
        <Responsive
          width={width}
          breakpoints={BREAKPOINTS}
          cols={COLS}
          layouts={layouts}
          rowHeight={ROW_HEIGHT}
          margin={MARGIN}
          dragConfig={{ enabled: editing, handle: ".dash-drag-handle" }}
          resizeConfig={{ enabled: editing }}
          onBreakpointChange={(_breakpoint, columns) => {
            cols.current = columns;
          }}
          // The stored layout changes when the user finishes a drag or a resize, or takes a
          // keyboard step. It is deliberately NOT updated from the grid's own layout-change report:
          // that report echoes whichever layout it last received, so writing it back sets two
          // states off against each other (React's "maximum update depth"), and it also fired on
          // load, marking an untouched dashboard as edited. Only 12-column layouts are stored: a
          // narrow screen's re-flow must not overwrite the desktop arrangement.
          onDragStop={(current) => {
            if (editing && cols.current === GRID_COLUMNS) onLayout?.(current);
          }}
          onResizeStop={(current) => {
            if (editing && cols.current === GRID_COLUMNS) onLayout?.(current);
          }}
        >
          {items.map((item) => (
            <div key={item.i}>
              <WidgetFrame
                item={item}
                onRemove={onRemove ? () => onRemove(item.i) : undefined}
                keyboard={
                  editing && onLayout
                    ? {
                        snapshot: () => positions(),
                        step: (from, key, resize) => {
                          // The widget's own place is the live one; the others come from the doc.
                          const layout = positions().map((p) => (p.i === from.i ? from : p));
                          const step = stepLayout(layout, from.i, key, resize);
                          if (step) onLayout(step.layout);
                          return step;
                        },
                        restore: (layout) => onLayout(layout),
                      }
                    : undefined
                }
              />
            </div>
          ))}
        </Responsive>
      ) : null}
    </div>
  );
}
