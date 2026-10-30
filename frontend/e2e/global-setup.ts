import { rmSync } from "node:fs";
import { AXE_DIR } from "./helpers/axe";

/** Clears the previous run's per-screen axe results, so the merged report has no stale screens. */
export default async function globalSetup(): Promise<void> {
  rmSync(AXE_DIR, { recursive: true, force: true });
}
