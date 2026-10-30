import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import {
  buildImportPathBody,
  buildRowsQuery,
  buildUploadForm,
  cleanOptions,
  describeImportError,
  hasOptions,
} from "./requests";

describe("import request builders", () => {
  it("drops unset and empty options", () => {
    expect(
      cleanOptions({ delimiter: "", encoding: " ", header: null, sheet: "", headerRow: null }),
    ).toEqual({});
    expect(hasOptions({})).toBe(false);
  });

  it("keeps meaningful options, including header=false and a header row", () => {
    expect(
      cleanOptions({ delimiter: ";", encoding: "latin-1", header: false, headerRow: 2 }),
    ).toEqual({ delimiter: ";", encoding: "latin-1", header: false, headerRow: 2 });
  });

  it("builds the import-path body with trimmed path and optional name", () => {
    expect(
      buildImportPathBody("  C:\\data\\a.csv ", { delimiter: "," }, { name: " Sales " }),
    ).toEqual({
      path: "C:\\data\\a.csv",
      options: { delimiter: "," },
      name: "Sales",
    });
    expect(buildImportPathBody("/x.csv", {})).toEqual({ path: "/x.csv", options: {} });
  });

  it("builds the upload form, sending options only when set", () => {
    const file = new File(["a,b"], "a.csv");
    const plain = buildUploadForm(file, {});
    expect(plain.has("options")).toBe(false);
    expect(plain.get("file")).toBeInstanceOf(File);
    const withOptions = buildUploadForm(file, { delimiter: ";" }, "My data");
    expect(JSON.parse(String(withOptions.get("options")))).toEqual({ delimiter: ";" });
    expect(withOptions.get("name")).toBe("My data");
  });
});

describe("buildRowsQuery", () => {
  it("always sends offset and limit", () => {
    expect(buildRowsQuery({ offset: 400, limit: 200 }).toString()).toBe("offset=400&limit=200");
  });

  it("repeats sort in priority order", () => {
    const q = buildRowsQuery({
      offset: 0,
      limit: 10,
      sort: [
        { field: "region", descending: false },
        { field: "sales", descending: true },
      ],
    });
    expect(q.getAll("sort")).toEqual(["region:asc", "sales:desc"]);
  });

  it("serialises filters as a JSON Predicate array", () => {
    const q = buildRowsQuery({
      offset: 0,
      limit: 10,
      filters: [{ op: "in", field: "region", values: ["North"] }],
    });
    expect(JSON.parse(q.get("filters") ?? "null")).toEqual([
      { op: "in", field: "region", values: ["North"] },
    ]);
    expect(buildRowsQuery({ offset: 0, limit: 1, filters: [] }).has("filters")).toBe(false);
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
