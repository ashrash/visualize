import { Download } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { ExportFormat } from "./saveChart";

const OPTIONS: { format: ExportFormat; label: string }[] = [
  { format: "png", label: "PNG image" },
  { format: "jpeg", label: "JPEG image" },
  { format: "svg", label: "SVG (vector)" },
  { format: "pdf", label: "PDF" },
  { format: "clipboard", label: "Copy image to clipboard" },
];

interface ExportMenuProps {
  /** Runs the export; reject with a readable message to show it. */
  onExport(format: ExportFormat): Promise<void>;
  disabled?: boolean;
}

/** A keyboard-friendly menu button: Enter/Space opens, arrows move, Escape closes. */
export function ExportMenu({ onExport, disabled }: ExportMenuProps) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onEscape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", onEscape);
    };
  }, [open]);

  useEffect(() => {
    if (!status) return;
    const timer = setTimeout(() => setStatus(null), 4000);
    return () => clearTimeout(timer);
  }, [status]);

  const run = async (format: ExportFormat) => {
    setOpen(false);
    try {
      await onExport(format);
      setStatus({ tone: "ok", text: format === "clipboard" ? "Copied" : "Exported" });
    } catch (error) {
      setStatus({ tone: "error", text: error instanceof Error ? error.message : "Export failed" });
    }
  };

  const moveFocus = (current: HTMLElement, delta: number) => {
    const items = [...(rootRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
    const index = items.indexOf(current);
    items[(index + delta + items.length) % items.length]?.focus();
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((value) => !value)}
        className="inline-flex items-center gap-1 rounded-md border border-line bg-bg px-2 py-1 text-xs text-fg hover:bg-surface focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50"
      >
        <Download size={14} aria-hidden="true" />
        Export
      </button>
      {open ? (
        <div
          id={menuId}
          role="menu"
          aria-label="Export chart"
          className="absolute right-0 z-20 mt-1 min-w-48 rounded-md border border-line bg-bg py-1 text-sm shadow-md"
        >
          {OPTIONS.map((option, index) => (
            <button
              key={option.format}
              type="button"
              role="menuitem"
              // biome-ignore lint/a11y/noAutofocus: moving focus into the opened menu is the expected behaviour
              autoFocus={index === 0}
              onClick={() => void run(option.format)}
              onKeyDown={(event) => {
                if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                  event.preventDefault();
                  moveFocus(event.currentTarget, event.key === "ArrowDown" ? 1 : -1);
                }
              }}
              className="block w-full px-3 py-1.5 text-left text-fg hover:bg-surface focus-visible:bg-surface focus-visible:outline-none"
            >
              {option.label}
            </button>
          ))}
        </div>
      ) : null}
      <span role="status" aria-live="polite" className="sr-only">
        {status?.text ?? ""}
      </span>
      {status ? (
        <span
          className={`absolute right-0 top-full mt-1 whitespace-nowrap text-xs ${status.tone === "error" ? "text-danger" : "text-muted"}`}
          aria-hidden="true"
        >
          {status.text}
        </span>
      ) : null}
    </div>
  );
}
