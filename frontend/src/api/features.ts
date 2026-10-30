/**
 * Feature flags for backend endpoints that are specified (types exist) but not merged yet.
 * Flip each to true when its backend lands. Each flag gates one slice of UI; when off, the old
 * behaviour (or a disabled control) stays. For local hand-testing against a backend branch,
 * override in the browser console: localStorage.setItem("viz.features", '{"stagedImport":true}').
 */
export interface Features {
  /** data-core: staged upload, live preview, import/refresh as jobs, jobs list + SSE. */
  stagedImport: boolean;
  /** data-core: dataset rename, delete (with force) and refresh. */
  datasetManagement: boolean;
  /** data-core: POST rows with sort/filters/search, and column values for filter popovers. */
  serverRows: boolean;
  /** data-core: POST /api/exports. */
  exports: boolean;
  /** transform: inspect, preview, formula check/functions, materialize. */
  pipelineApi: boolean;
  /** platform: rename, duplicate, delete, restore, version preview, .vizproj import/export. */
  platformProjects: boolean;
}

const DEFAULTS: Features = {
  stagedImport: false,
  datasetManagement: false,
  serverRows: false,
  exports: false,
  pipelineApi: false,
  platformProjects: false,
};

function overrides(): Partial<Features> {
  try {
    const raw = localStorage.getItem("viz.features");
    return raw ? (JSON.parse(raw) as Partial<Features>) : {};
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
