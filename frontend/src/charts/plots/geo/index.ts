import type { PlotEntry } from "../../core/types";
import { bubbleMap } from "./bubbleMap";
import { cartogram } from "./cartogram";
import { choropleth } from "./choropleth";
import { dotDensity } from "./dotDensity";
import { geoHeatmap } from "./geoHeatmap";

// Plots.md Section 7: choropleth, bubble map, dot density, cartogram, geographic heatmap.
const entries: PlotEntry[] = [choropleth, bubbleMap, dotDensity, geoHeatmap, cartogram];
export default entries;
