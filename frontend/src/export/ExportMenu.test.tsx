// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ExportMenu } from "./ExportMenu";

afterEach(cleanup);

const open = () => fireEvent.click(screen.getByRole("button", { name: /Export/ }));
const items = () => screen.getAllByRole("menuitem");

describe("ExportMenu keyboard behavior", () => {
  it("lists the five formats and opens with ArrowDown on the trigger", () => {
    render(<ExportMenu onExport={vi.fn()} />);
    fireEvent.keyDown(screen.getByRole("button", { name: /Export/ }), { key: "ArrowDown" });
    expect(items().map((i) => i.textContent)).toEqual([
      "PNG image",
      "JPEG image",
      "SVG (vector)",
      "PDF",
      "Copy image to clipboard",
    ]);
  });

  it("Home and End jump to the first and last item; arrows wrap", () => {
    render(<ExportMenu onExport={vi.fn()} />);
    open();
    items()[0]?.focus();
    fireEvent.keyDown(items()[0] as HTMLElement, { key: "End" });
    expect(document.activeElement).toBe(items().at(-1));
    fireEvent.keyDown(items().at(-1) as HTMLElement, { key: "ArrowDown" });
    expect(document.activeElement).toBe(items()[0]);
    fireEvent.keyDown(items()[0] as HTMLElement, { key: "Home" });
    expect(document.activeElement).toBe(items()[0]);
  });

  it("Escape closes and returns focus to the trigger", () => {
    render(<ExportMenu onExport={vi.fn()} />);
    open();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: /Export/ }));
  });

  it("Tab closes the menu and returns focus to the trigger", () => {
    render(<ExportMenu onExport={vi.fn()} />);
    open();
    fireEvent.keyDown(items()[1] as HTMLElement, { key: "Tab" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: /Export/ }));
  });

  it("choosing an item exports, closes, and returns focus to the trigger", async () => {
    const onExport = vi.fn(async () => {});
    render(<ExportMenu onExport={onExport} />);
    open();
    await act(async () => {
      fireEvent.click(screen.getByRole("menuitem", { name: "SVG (vector)" }));
    });
    expect(onExport).toHaveBeenCalledWith("svg");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: /Export/ }));
  });

  it("shows an export failure to the user", async () => {
    const onExport = vi.fn(async () => {
      throw new Error("Copying needs a secure page");
    });
    render(<ExportMenu onExport={onExport} />);
    open();
    await act(async () => {
      fireEvent.click(screen.getByRole("menuitem", { name: "PDF" }));
    });
    expect(screen.getAllByText("Copying needs a secure page").length).toBeGreaterThan(0);
  });
});
