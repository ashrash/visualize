/**
 * Feature flags for backend endpoints that are specified but not merged yet. Flip one to true
 * when its backend lands, and delete the flag once the feature is simply on. For local
 * hand-testing, override in the browser console:
 * localStorage.setItem("viz.features", '{"platformProjects":false}').
 */
export interface Features {
  /** platform: rename, duplicate, delete, restore, version preview, .vizproj import/export. */
  platformProjects: boolean;
  /**
   * stats (Wave 2): correlation, regression, t-tests, ANOVA, chi-square, distribution fitting,
   * outliers and confidence intervals. TODO(stats): flip on once the `stats` agent's Wave 2
   * endpoints merge; see frontend/src/api/stats.ts for the provisional request/response shapes.
   */
  statsTests: boolean;
}

const DEFAULTS: Features = {
  platformProjects: true,
  statsTests: false,
};

/** Only known flags with boolean values are accepted from localStorage; anything else is ignored. */
function overrides(): Partial<Features> {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem("viz.features") ?? "null");
    if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return {};
    const out: Partial<Features> = {};
    for (const key of Object.keys(DEFAULTS) as (keyof Features)[]) {
      const value = (raw as Record<string, unknown>)[key];
      if (typeof value === "boolean") out[key] = value;
    }
    return out;
  } catch {
    return {};
  }
}

/** Current flags (read at call time so overrides and tests take effect without a rebuild). */
export function features(): Features {
  return { ...DEFAULTS, ...overrides() };
}

export function featureOn(name: keyof Features): boolean {
  return features()[name];
}
