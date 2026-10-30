import { createServer } from "node:net";

/** Ask the OS for a free TCP port on localhost. */
export function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (address === null || typeof address === "string") {
        server.close();
        reject(new Error("could not read the port"));
        return;
      }
      server.close(() => resolve(address.port));
    });
  });
}
