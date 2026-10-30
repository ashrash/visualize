import { afterEach, describe, expect, it } from "vitest";
import { featureOn, features } from "./features";

afterEach(() => localStorage.clear());

const set = (value: unknown) => localStorage.setItem("viz.features", JSON.stringify(value));

describe("feature flags", () => {
  it("has the merged backends on by default", () => {
    expect(featureOn("platformProjects")).toBe(true);
  });

  it("has the not-yet-merged Wave 2 stats tests off by default", () => {
    expect(featureOn("statsTests")).toBe(false);
  });

  it("accepts boolean overrides for known flags", () => {
    set({ platformProjects: false });
    expect(featureOn("platformProjects")).toBe(false);
  });

  it("ignores non-boolean values, unknown flags and malformed JSON", () => {
    set({ platformProjects: 0, pipelineApi: false, bogus: true });
    expect(features().platformProjects).toBe(true);
    expect("bogus" in features()).toBe(false);
    expect("pipelineApi" in features()).toBe(false); // that flag was removed
    localStorage.setItem("viz.features", "{not json");
    expect(features().platformProjects).toBe(true);
    set(["platformProjects"]);
    expect(features().platformProjects).toBe(true);
  });
});
