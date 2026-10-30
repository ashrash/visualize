import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import type { Column } from "../../api/types";
import { changePredicateOp, createPredicate, type Scalar } from "./predicates";
import { ScalarInput } from "./ScalarInput";

afterEach(cleanup);

const numeric: Column = {
  name: "units",
  type: "DOUBLE",
  semantic: "quantitative",
  nullCount: 0,
  approxDistinct: 10,
};
const nominal: Column = { ...numeric, name: "city", type: "VARCHAR", semantic: "nominal" };

function ScalarHarness({ column, initial }: { column: Column; initial: Scalar }) {
  const [value, setValue] = useState<Scalar>(initial);
  return (
    <>
      <ScalarInput id="x" column={column} value={value} onChange={setValue} />
      <output data-testid="value">{JSON.stringify(value)}</output>
      <button type="button" onClick={() => setValue("reset")}>
        external
      </button>
    </>
  );
}

function ListHarness({ column }: { column: Column }) {
  const [value, setValue] = useState<Scalar[]>([]);
  return (
    <>
      <ScalarInput list id="x" column={column} value={value} onChange={setValue} />
      <output data-testid="value">{JSON.stringify(value)}</output>
    </>
  );
}

const input = () => document.getElementById("x") as HTMLInputElement;
const type = (text: string) => fireEvent.change(input(), { target: { value: text } });
const stored = () => screen.getByTestId("value").textContent;

describe("ScalarInput keeps what the user types", () => {
  it("keeps a trailing space so multi-word values can be typed", () => {
    render(<ScalarHarness column={nominal} initial={null} />);
    type("New");
    type("New ");
    expect(input().value).toBe("New ");
    type("New York");
    expect(input().value).toBe("New York");
    expect(stored()).toBe('"New York"');
  });

  it("keeps a trailing dot so decimals can be typed", () => {
    render(<ScalarHarness column={numeric} initial={null} />);
    type("1");
    type("1.");
    expect(input().value).toBe("1.");
    type("1.5");
    expect(input().value).toBe("1.5");
    expect(stored()).toBe("1.5");
  });

  it("keeps a trailing comma so a second list value can be typed", () => {
    render(<ListHarness column={nominal} />);
    type("a");
    type("a,");
    expect(input().value).toBe("a,");
    type("a, b");
    expect(stored()).toBe('["a","b"]');
    expect(input().value).toBe("a, b");
  });

  it("shows an outside change of the value (undo, reload)", () => {
    render(<ScalarHarness column={nominal} initial="x" />);
    type("typed");
    fireEvent.click(screen.getByText("external"));
    expect(input().value).toBe("reset");
  });
});

describe("changePredicateOp keeps compatible values", () => {
  it("eq <-> in", () => {
    const eq = { ...createPredicate("eq", "a"), value: "x" } as ReturnType<typeof createPredicate>;
    const asIn = changePredicateOp(eq, "in");
    expect(asIn).toMatchObject({ op: "in", values: ["x"] });
    expect(changePredicateOp(asIn, "eq")).toMatchObject({ op: "eq", value: "x" });
    expect(changePredicateOp(asIn, "not_in")).toMatchObject({ op: "not_in", values: ["x"] });
  });

  it("range <-> between_dates keep the bounds", () => {
    const range = { ...createPredicate("range", "a"), min: 1, max: 5 } as ReturnType<
      typeof createPredicate
    >;
    expect(changePredicateOp(range, "between_dates")).toMatchObject({ min: "1", max: "5" });
  });

  it("drops values that have no meaning in the new op", () => {
    const eq = { ...createPredicate("eq", "a"), value: "x" } as ReturnType<typeof createPredicate>;
    expect(changePredicateOp(eq, "is_null")).toMatchObject({ op: "is_null", isNull: true });
  });
});
