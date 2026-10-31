/** Trailing debounce with `flush` and `cancel`: brush drags and slider drags commit once. */
export interface Debounced<A extends unknown[]> {
  (...args: A): void;
  /** Run the pending call now (e.g. on mouse-up). */
  flush(): void;
  cancel(): void;
}

export function debounce<A extends unknown[]>(fn: (...args: A) => void, ms: number): Debounced<A> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let pending: A | undefined;
  const run = () => {
    timer = undefined;
    const args = pending;
    pending = undefined;
    if (args) fn(...args);
  };
  const debounced = ((...args: A) => {
    pending = args;
    if (timer !== undefined) clearTimeout(timer);
    timer = setTimeout(run, ms);
  }) as Debounced<A>;
  debounced.flush = () => {
    if (timer !== undefined) clearTimeout(timer);
    run();
  };
  debounced.cancel = () => {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
    pending = undefined;
  };
  return debounced;
}

/** Brush drags and slider drags wait this long after the last change before filtering. */
export const BRUSH_DEBOUNCE_MS = 150;
