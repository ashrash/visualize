import type { PlotEntry } from "../../core/types";
import { bar } from "./bar";

// Plots.md §1. Phase 0 ships `bar`; ui-charts adds the rest of the family.
const entries: PlotEntry[] = [bar];
export default entries;
