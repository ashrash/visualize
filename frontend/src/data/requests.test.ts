import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { cleanOptions, describeImportError } from "./requests";

describe("import request builders", () => {
  it("drops unset and empty options", () => {
    expect(
      cleanOptions({ delimiter: "", encoding: " ", header: null, sheet: "", headerRow: null }),
    ).toEqual({});
  });

  it("keeps meaningful options, including header=false and a header row", () => {
    expect(
      cleanOptions({ delimiter: ";", encoding: "latin-1", header: false, headerRow: 2 }),
    ).toEqual({ delimiter: ";", encoding: "latin-1", header: false, headerRow: 2 });
  });
});

describe("describeImportError", () => {
  it("explains each documented status", () => {
    expect(describeImportError(new ApiError("dup", 409))).toMatch(/Conflict: dup/);
    expect(describeImportError(new ApiError("big", 413))).toMatch(/too large/);
    expect(describeImportError(new ApiError("bad", 415))).toMatch(/Unsupported file type/);
    expect(describeImportError(new ApiError("bad csv", 422))).toMatch(/bad csv/);
    expect(describeImportError(new ApiError("oops", 500))).toBe("oops");
    expect(describeImportError(new Error("net"))).toBe("net");
  });
});
