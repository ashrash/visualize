import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { UpdateInfo } from "../api/types";
import { UpdateIndicator } from "./UpdateIndicator";

const IDLE: UpdateInfo = {
  status: "idle",
  currentVersion: "1.0.0",
  latestVersion: null,
  releaseUrl: null,
  canApply: false,
  progress: null,
  message: null,
  error: null,
  checkedAt: null,
};

const requests: { url: string; method: string }[] = [];
let statusResponse: UpdateInfo = IDLE;
let applyResponse: UpdateInfo | null = null;

beforeEach(() => {
  requests.length = 0;
  statusResponse = IDLE;
  applyResponse = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = typeof input === "string" || input instanceof URL ? null : input;
      const url = new URL(request ? request.url : String(input));
      const method = request?.method ?? init?.method ?? "GET";
      requests.push({ url: url.pathname, method });
      if (url.pathname === "/api/update/status") {
        return jsonResponse(statusResponse);
      }
      if (url.pathname === "/api/update/apply" && method === "POST") {
        return jsonResponse(applyResponse ?? statusResponse, 202);
      }
      return jsonResponse({}, 404);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const renderIndicator = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <UpdateIndicator />
    </QueryClientProvider>,
  );

describe("UpdateIndicator", () => {
  it("renders nothing when already up to date", async () => {
    const { container } = renderIndicator();
    await new Promise((r) => setTimeout(r, 20));
    expect(container.textContent).toBe("");
  });

  it("shows an available update and lets the user apply it", async () => {
    statusResponse = {
      ...IDLE,
      status: "available",
      latestVersion: "2.0.0",
      releaseUrl: "https://github.com/owner/repo/releases/tag/v2.0.0",
      canApply: true,
    };
    applyResponse = { ...statusResponse, status: "applying", message: "Downloading" };
    renderIndicator();
    const button = await screen.findByRole("button", { name: "Update to 2.0.0" });
    fireEvent.click(button);
    expect(await screen.findByText("Version 2.0.0 is available")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Update now" }));
    await screen.findByRole("button", { name: "Updating…" });
    expect(requests).toContainEqual({ url: "/api/update/apply", method: "POST" });
  });

  it("shows progress while an update is applying", async () => {
    statusResponse = { ...IDLE, status: "applying", progress: 0.5, message: "Installing" };
    renderIndicator();
    const button = await screen.findByRole("button", { name: "Updating…" });
    fireEvent.click(button);
    const progress = await screen.findByLabelText("Update progress");
    expect(progress.getAttribute("value")).toBe("0.5");
    expect(screen.getByText("Installing")).toBeTruthy();
  });

  it("offers no apply button for a dev checkout that can't self-update", async () => {
    statusResponse = {
      ...IDLE,
      status: "available",
      latestVersion: "2.0.0",
      releaseUrl: "https://github.com/owner/repo/releases/tag/v2.0.0",
      canApply: false,
    };
    renderIndicator();
    const button = await screen.findByRole("button", { name: "Update to 2.0.0" });
    fireEvent.click(button);
    await screen.findByText(/can't update itself/);
    expect(screen.queryByRole("button", { name: "Update now" })).toBeNull();
    expect(screen.getByRole("link", { name: "release notes" })).toHaveProperty(
      "href",
      "https://github.com/owner/repo/releases/tag/v2.0.0",
    );
  });

  it("shows a restart prompt once the update is done", async () => {
    statusResponse = { ...IDLE, status: "done", message: "Updated to 2.0.0. Restart the app." };
    renderIndicator();
    const button = await screen.findByRole("button", { name: "Update installed" });
    fireEvent.click(button);
    expect(await screen.findByText("Restart the app to finish.")).toBeTruthy();
  });

  it("shows the error when an update fails", async () => {
    statusResponse = { ...IDLE, status: "failed", error: "The update failed." };
    renderIndicator();
    const button = await screen.findByRole("button", { name: "Update failed" });
    fireEvent.click(button);
    expect(await screen.findByText("The update failed.")).toBeTruthy();
  });
});
