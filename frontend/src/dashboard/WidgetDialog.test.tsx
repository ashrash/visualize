// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createDashboard } from "./model";

const uploadImage = vi.fn();
vi.mock("./assetsApi", () => ({
  IMAGE_ACCEPT: "image/png",
  assetUrl: (id: string) => `/api/assets/${id}`,
  uploadImage: (...a: unknown[]) => uploadImage(...a),
}));
vi.mock("../charts", () => ({ FORMAT_PRESETS: [] }));
vi.mock("../data/hooks", () => ({ useDatasets: () => ({ data: [] }) }));

import { DashboardEnvProvider } from "./env";
import { WidgetDialog } from "./WidgetDialog";

function open(onAdd = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DashboardEnvProvider
        value={{
          dashboard: createDashboard(),
          charts: [],
          pipelines: [],
          globalFilters: [],
          mode: "edit",
          columnsOf: () => undefined,
        }}
      >
        <WidgetDialog kind="image" onClose={() => {}} onAdd={onAdd} />
      </DashboardEnvProvider>
    </QueryClientProvider>,
  );
  return onAdd;
}

afterEach(() => {
  cleanup();
  uploadImage.mockReset();
});

describe("adding an image widget", () => {
  it("uploads the chosen file and adds a widget that points at the stored asset", async () => {
    uploadImage.mockResolvedValue({
      id: "asset-7",
      contentType: "image/png",
      sizeBytes: 3,
      url: "",
    });
    const onAdd = open();
    const add = screen.getByRole("button", { name: "Add" });
    expect((add as HTMLButtonElement).disabled).toBe(true); // nothing uploaded yet

    const file = new File(["x"], "company_logo.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("Image"), { target: { files: [file] } });
    await waitFor(() => expect(screen.getByAltText("Preview of the upload")).toBeTruthy());
    expect(uploadImage.mock.calls[0]?.[0]).toBe(file);
    // alt text starts from the file name, and can be edited
    expect((screen.getByLabelText("Alt text") as HTMLInputElement).value).toBe("company logo");

    fireEvent.click(add);
    expect(onAdd).toHaveBeenCalledWith({
      widget: { kind: "image", assetId: "asset-7", alt: "company logo", fit: "contain" },
    });
  });

  it("shows why an upload was refused and adds nothing", async () => {
    uploadImage.mockRejectedValue(new Error("unsupported image type"));
    const onAdd = open();
    fireEvent.change(screen.getByLabelText("Image"), {
      target: { files: [new File(["x"], "notes.txt")] },
    });
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText("unsupported image type")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Add" }) as HTMLButtonElement).disabled).toBe(true);
    expect(onAdd).not.toHaveBeenCalled();
  });
});
