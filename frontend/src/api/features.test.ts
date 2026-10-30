import { afterEach, describe, expect, it } from "vitest";
import { featureOn, features } from "./features";

afterEach(() => localStorage.clear());

const set = (value: unknown) => localStorage.setItem("viz.features", JSON.stringify(value));

describe("feature flags", () => {
  it("has merged backends on and transform's pipeline API off by default", () => {
    expect(featureOn("platformProjects")).toBe(true);
    expect(featureOn("pipelineApi")).toBe(false);
  });

  it("accepts boolean overrides for known flags", () => {
    set({ pipelineApi: true, platformProjects: false });
    expect(featureOn("pipelineApi")).toBe(true);
    expect(featureOn("platformProjects")).toBe(false);
  });

  it("ignores non-boolean values, unknown flags and malformed JSON", () => {
    set({ pipelineApi: "yes", platformProjects: 0, bogus: true });
    expect(features().pipelineApi).toBe(false);
    expect(features().platformProjects).toBe(true);
    expect("bogus" in features()).toBe(false);
    localStorage.setItem("viz.features", "{not json");
    expect(features().pipelineApi).toBe(false);
    set(["pipelineApi"]);
    expect(features().pipelineApi).toBe(false);
  });
});
