import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { createAutosaver, type SaveStatus } from "./autosave";

function setup(save: (autosave: boolean) => Promise<void>, initialDirty = true) {
  const statuses: SaveStatus[] = [];
  let dirty = initialDirty;
  let saving = false;
  let editedDuringSave = false;
  const saver = createAutosaver({
    delayMs: 2000,
    save: async (autosave) => {
      saving = true;
      editedDuringSave = false;
      await save(autosave);
      saving = false;
      dirty = editedDuringSave;
    },
    setStatus: (status) => statuses.push(status),
    isDirty: () => dirty,
  });
  /** Simulate a user edit: dirty, and the autosaver is told. */
  const edit = () => {
    dirty = true;
    if (saving) editedDuringSave = true;
    saver.schedule();
  };
  return { saver, statuses, edit };
}

describe("autosaver", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("saves once, 2 s after the last change", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const { saver, statuses } = setup(save);
    saver.schedule();
    await vi.advanceTimersByTimeAsync(1500);
    saver.schedule(); // another edit restarts the timer
    await vi.advanceTimersByTimeAsync(1500);
    expect(save).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(600);
    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith(true);
    expect(statuses).toEqual(["unsaved", "unsaved", "saving", "saved"]);
  });

  it("flush saves immediately and manually (autosave=false)", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const { saver } = setup(save);
    saver.schedule();
    await saver.flush(false);
    expect(save).toHaveBeenCalledWith(false);
    await vi.advanceTimersByTimeAsync(5000);
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("autosave flush does nothing when clean", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const { saver } = setup(save, false);
    await saver.flush(true);
    expect(save).not.toHaveBeenCalled();
  });

  it("reports an error and recovers on the next edit", async () => {
    const save = vi.fn().mockRejectedValueOnce(new Error("boom")).mockResolvedValue(undefined);
    const { saver, statuses } = setup(save);
    saver.schedule();
    await vi.advanceTimersByTimeAsync(2100);
    expect(statuses.at(-1)).toBe("error");
    saver.schedule();
    await vi.advanceTimersByTimeAsync(2100);
    expect(statuses.at(-1)).toBe("saved");
  });

  it("pauses on a 409 conflict until resumed", async () => {
    const save = vi.fn().mockRejectedValue(new ApiError("stale revision", 409));
    const { saver, statuses } = setup(save);
    saver.schedule();
    await vi.advanceTimersByTimeAsync(2100);
    expect(statuses.at(-1)).toBe("conflict");
    saver.schedule();
    await vi.advanceTimersByTimeAsync(5000);
    expect(save).toHaveBeenCalledTimes(1);
    saver.resume();
    expect(statuses.at(-1)).toBe("saved");
  });

  it("saves again when edits arrive during an in-flight save", async () => {
    let release: () => void = () => undefined;
    const save = vi
      .fn()
      .mockImplementationOnce(() => new Promise<void>((resolve) => (release = resolve)))
      .mockResolvedValue(undefined);
    const { saver, statuses, edit } = setup(save);
    saver.schedule();
    await vi.advanceTimersByTimeAsync(2100);
    expect(save).toHaveBeenCalledTimes(1);
    edit();
    release();
    await vi.advanceTimersByTimeAsync(0);
    expect(statuses.at(-1)).toBe("unsaved");
    await vi.advanceTimersByTimeAsync(2100);
    expect(save).toHaveBeenCalledTimes(2);
    expect(statuses.at(-1)).toBe("saved");
  });
});
