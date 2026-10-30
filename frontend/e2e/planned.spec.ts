import { test } from "./fixtures";

/**
 * Flows whose UI is not ready yet. Each is a `test.fixme` with the reason on one line, so the
 * report lists them as tracked work. When the feature lands, remove `.fixme` and fill in the steps
 * (the intended flow is in the title). Owners follow CLAUDE.md section 9.
 */
test.describe("planned flows", () => {
  // The two pipeline flows that used to be fixme'd here now live in pipeline.spec.ts: the
  // pipelineApi flag is gone and transform's compiler/preview/materialize endpoints are merged.

  test.fixme("dashboard: add three charts, drag and resize them, save, reload", {
    annotation: { type: "blocked", description: "dashboards are Wave 3 (ui-dashboard)" },
  }, async () => {});

  test.fixme("cross-filter: clicking a bar filters the other charts in its link group", {
    annotation: {
      type: "blocked",
      description: "cross-filter bus and click-to-filter are Wave 3 (ui-dashboard)",
    },
  }, async () => {});

  test.fixme("share: create a share token and open /share/:token read-only in a fresh context", {
    annotation: {
      type: "blocked",
      description: "share endpoints and view are Wave 3 (platform, ui-dashboard)",
    },
  }, async () => {});

  test.fixme("embed: /embed/:token renders in an iframe snippet without app chrome", {
    annotation: { type: "blocked", description: "embed view is Wave 3 (platform, ui-dashboard)" },
  }, async () => {});
});
