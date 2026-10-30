import type { PlotEntry } from "../../core/types";
import { lorenz, precisionRecall, quantile, roc } from "./curves";
import { eventStudy } from "./eventStudy";
import { forest } from "./forest";
import { impulseResponse } from "./irf";
import { kaplanMeier } from "./km";
import { coefficient, marginalEffects } from "./models";

// Plots.md §8: Kaplan-Meier, forest, ROC, precision-recall, Lorenz, quantile, marginal effects,
// coefficients, impulse responses, event study. Statistics run on the server
// (backend plots/econometric.py); these renderers only draw the StatResult.
const entries: PlotEntry[] = [
  kaplanMeier,
  forest,
  roc,
  precisionRecall,
  lorenz,
  quantile,
  marginalEffects,
  coefficient,
  impulseResponse,
  eventStudy,
];
export default entries;
