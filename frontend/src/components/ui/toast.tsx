import { X } from "lucide-react";
import { Toast as ToastPrimitive } from "radix-ui";
import { create } from "zustand";
import { cn } from "./cn";

export interface ToastMessage {
  id: number;
  title: string;
  description?: string;
  variant: "default" | "error" | "success";
}

interface ToastStore {
  toasts: ToastMessage[];
  push: (toast: Omit<ToastMessage, "id" | "variant"> & { variant?: ToastMessage["variant"] }) => void;
  dismiss: (id: number) => void;
}

let nextId = 1;

export const useToastStore = create<ToastStore>((set) => ({
  toasts: [],
  push: (toast) =>
    set((state) => ({
      toasts: [...state.toasts, { variant: "default", ...toast, id: nextId++ }],
    })),
  dismiss: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
}));

/** Show a toast from anywhere (components or plain functions). */
export function toast(
  title: string,
  options: { description?: string; variant?: ToastMessage["variant"] } = {},
): void {
  useToastStore.getState().push({ title, ...options });
}

const variantClass: Record<ToastMessage["variant"], string> = {
  default: "border-line",
  error: "border-danger",
  success: "border-success",
};

/** Mount once near the root. */
export function Toaster() {
  const toasts = useToastStore((state) => state.toasts);
  const dismiss = useToastStore((state) => state.dismiss);
  return (
    <ToastPrimitive.Provider swipeDirection="right" duration={5000}>
      {toasts.map((t) => (
        <ToastPrimitive.Root
          key={t.id}
          type={t.variant === "error" ? "foreground" : "background"}
          onOpenChange={(open) => {
            if (!open) dismiss(t.id);
          }}
          className={cn(
            "flex items-start gap-2 rounded-md border bg-bg p-3 text-sm shadow-lg",
            variantClass[t.variant],
          )}
        >
          <div className="min-w-0 flex-1">
            <ToastPrimitive.Title className="font-medium">{t.title}</ToastPrimitive.Title>
            {t.description ? (
              <ToastPrimitive.Description className="mt-0.5 text-muted">
                {t.description}
              </ToastPrimitive.Description>
            ) : null}
          </div>
          <ToastPrimitive.Close
            aria-label="Dismiss"
            className="rounded p-0.5 text-muted hover:bg-surface-2"
          >
            <X size={14} aria-hidden="true" />
          </ToastPrimitive.Close>
        </ToastPrimitive.Root>
      ))}
      <ToastPrimitive.Viewport className="fixed right-4 bottom-4 z-[70] flex w-80 max-w-[92vw] flex-col gap-2 outline-none" />
    </ToastPrimitive.Provider>
  );
}
