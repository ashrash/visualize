import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { debounce } from "./debounce";

describe("debounce", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("runs once with the last arguments after the quiet period", () => {
    const fn = vi.fn();
    const d = debounce(fn, 150);
    d(1);
    vi.advanceTimersByTime(100);
    d(2);
    vi.advanceTimersByTime(100);
    expect(fn).not.toHaveBeenCalled();
    vi.advanceTimersByTime(60);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledWith(2);
  });

  it("flush runs the pending call immediately, cancel drops it", () => {
    const fn = vi.fn();
    const d = debounce(fn, 150);
    d("a");
    d.flush();
    expect(fn).toHaveBeenCalledWith("a");
    d("b");
    d.cancel();
    vi.advanceTimersByTime(500);
    expect(fn).toHaveBeenCalledTimes(1);
    d.flush(); // nothing pending
    expect(fn).toHaveBeenCalledTimes(1);
  });
});
