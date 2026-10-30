import type { PlotEntry } from "../../core/types";
import { andrewsCurves } from "./andrews";
import { cluster } from "./cluster";
import { dendrogram } from "./dendrogram";
import { factorLoading } from "./factor";
import { parallelCoordinates } from "./parallel";
import { pcaBiplot } from "./pca";
import { radar } from "./radar";

// Plots.md §6: radar, parallel coordinates, PCA biplot, factor loadings, cluster plot,
// dendrogram, Andrews curves. Statistics run on the server (backend plots/multivariate.py).
const entries: PlotEntry[] = [
  radar,
  parallelCoordinates,
  pcaBiplot,
  factorLoading,
  cluster,
  dendrogram,
  andrewsCurves,
];
export default entries;
