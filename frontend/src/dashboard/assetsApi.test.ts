import { afterEach, describe, expect, it, vi } from "vitest";
import { assetUrl, uploadImage } from "./assetsApi";

const asset = {
  id: "abc",
  contentType: "image/png",
  sizeBytes: 3,
  url: "/api/assets/abc",
};

describe("uploadImage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts the file as multipart field `file` and returns the stored asset", async () => {
    const sent: Request[] = [];
    vi.stubGlobal("fetch", async (request: Request) => {
      sent.push(request);
      return new Response(JSON.stringify(asset), {
        status: 201,
        headers: { "content-type": "application/json" },
      });
    });
    const file = new File([new Uint8Array([1, 2, 3])], "logo.png", { type: "image/png" });
    await expect(uploadImage(file)).resolves.toEqual(asset);
    const request = sent[0] as Request;
    expect(request.method).toBe("POST");
    expect(new URL(request.url).pathname).toBe("/api/assets");
    const form = await request.formData();
    const part = form.get("file") as File;
    expect(part.size).toBe(3);
  });

  it("surfaces the server's reason for a refused file (unsupported type, too large)", async () => {
    for (const [status, detail] of [
      [415, "unsupported image type"],
      [413, "file is larger than 10 MB"],
    ] as const) {
      vi.stubGlobal(
        "fetch",
        async () =>
          new Response(JSON.stringify({ detail }), {
            status,
            headers: { "content-type": "application/json" },
          }),
      );
      await expect(uploadImage(new File(["x"], "x.bin"))).rejects.toMatchObject({
        status,
        message: detail,
      });
    }
  });

  it("builds the URL an image is served from, encoding the id", () => {
    expect(assetUrl("a/b c")).toBe("/api/assets/a%2Fb%20c");
  });
});
