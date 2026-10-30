import { afterEach, describe, expect, it } from "vitest";
import { featureOn, features } from "./features";

afterEach(() => localStorage.clear());

const set = (value: unknown) => localStorage.setItem("viz.features", JSON.stringify(value));

describe("feature flags", () => {
  it("has platform projects on and the unmerged backends off by default", () => {
    expect(featureOn("platformProjects")).toBe(true);
    expect(featureOn("stagedImport")).toBe(false);
  });

  it("accepts boolean overrides for known flags", () => {
    set({ stagedImport: true, platformProjects: false });
    expect(featureOn("stagedImport")).toBe(true);
    expect(featureOn("platformProjects")).toBe(false);
  });

  it("ignores non-boolean values, unknown flags and malformed JSON", () => {
    set({ stagedImport: "yes", exports: 1, bogus: true });
    expect(features().stagedImport).toBe(false);
    expect(features().exports).toBe(false);
    expect("bogus" in features()).toBe(false);
    localStorage.setItem("viz.features", "{not json");
    expect(features().serverRows).toBe(false);
    set(["stagedImport"]);
    expect(features().stagedImport).toBe(false);
  });
});
