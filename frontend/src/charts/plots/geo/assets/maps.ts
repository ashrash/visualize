/**
 * Offline bundled maps (CLAUDE.md Section 8, Plots.md Section 7: "maps work offline").
 *
 * The GeoJSON here is derived from Natural Earth (public domain, naturalearthdata.com)
 * admin-0 (countries) and admin-1 (US states) layers at 1:110m and 1:50m, simplified and
 * stripped to {name, alt, iso2} properties by `./build_assets.py` (run it again to pick up a
 * newer Natural Earth release or a resolution/coverage change); no basemap tiles ship in the
 * MVP.
 *
 * Each file is dynamically imported (code-split, never in the main bundle) and registered
 * with ECharts under its own map name, so it is fetched only the first time a geo chart that
 * needs it renders. A chart's `params.level`/`params.resolution` pick which one.
 */
import { echarts } from "../echarts";

// Natural Earth's missing-value sentinel (see build_assets.py's MISSING constant): the build
// script already drops it, but a defensive second check here means a region column whose own
// missing-value sentinel happens to be the literal text "-99" still won't match anything.
const MISSING = "-99";

export type MapLevel = "country" | "us_state";
export type MapResolution = "110m" | "50m";

interface RawFeature {
  type: "Feature";
  properties: { name: string; alt: string[]; iso2?: string | null };
  geometry: unknown;
}

interface RawFeatureCollection {
  type: "FeatureCollection";
  features: RawFeature[];
}

/** A loaded map: its ECharts map name, and an index for matching data values to features. */
export interface MapIndex {
  mapName: string;
  /** Normalized key (an upper-case code, or a normalized name) -> the feature's canonical name. */
  byKey: Map<string, string>;
  /** Every canonical (registered) feature name, for "did this one draw?" checks. */
  canonicalNames: Set<string>;
}

// The JSON's inferred literal type (e.g. `type: "FeatureCollection"` widened to `string`,
// and a geometry union per feature) doesn't structurally match RawFeatureCollection, so each
// loader asserts the shape explicitly; the build script that generates these files is the
// source of truth for it.
const LOADERS: Record<string, () => Promise<{ default: RawFeatureCollection }>> = {
  "country:110m": () =>
    import("./countries-110m.json") as Promise<{ default: RawFeatureCollection }>,
  "country:50m": () => import("./countries-50m.json") as Promise<{ default: RawFeatureCollection }>,
  "us_state:110m": () =>
    import("./us-states-110m.json") as Promise<{ default: RawFeatureCollection }>,
  "us_state:50m": () =>
    import("./us-states-50m.json") as Promise<{ default: RawFeatureCollection }>,
};

const cache = new Map<string, Promise<MapIndex>>();

/** Lower-case, alphanumerics-and-spaces only: "Côte d'Ivoire" and "cote divoire" both match. */
export function normalizeName(name: string): string {
  return name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

export function mapKey(level: MapLevel, resolution: MapResolution): string {
  return `${level}:${resolution}`;
}

/** Fetch (once) and register the map for `level`/`resolution`, and build its match index. */
export function loadMap(level: MapLevel, resolution: MapResolution): Promise<MapIndex> {
  const key = mapKey(level, resolution);
  const cached = cache.get(key);
  if (cached) return cached;
  const loader = LOADERS[key];
  if (!loader) throw new Error(`no bundled map for ${key}`);
  const promise = loader().then((mod) => {
    const geojson = mod.default;
    const mapName = `viz-${key}`;
    // registerMap's type expects the full GeoJSON.GeoJSON shape; our trimmed features satisfy
    // it at runtime (ECharts reads only `type`, `properties` and `geometry`).
    echarts.registerMap(mapName, geojson as unknown as Parameters<typeof echarts.registerMap>[1]);
    const canonicalNames = new Set<string>();
    // The feature's own code (`name`, e.g. an ISO3) is authoritative and set directly: the
    // build script guarantees it is unique. Every other candidate key (a normalized display
    // name, an ISO2 code, an alternate name) is only a *claim*: two different regions can
    // normalize to, or historically share, the same abbreviation (seen in the 50m admin-1
    // layer) or ISO2 code. A key more than one region wants is ambiguous and is left out of
    // the index entirely, rather than resolved by whichever region happened to load last,
    // which would silently mismatch one of them.
    const byKey = new Map<string, string>();
    const claims = new Map<string, Set<string>>();
    const claim = (key: string, name: string) => {
      if (!key || key === MISSING.toLowerCase() || key === MISSING) return;
      const owners = claims.get(key) ?? new Set<string>();
      owners.add(name);
      claims.set(key, owners);
    };
    for (const feature of geojson.features) {
      const { name, alt, iso2 } = feature.properties;
      canonicalNames.add(name);
      byKey.set(name.toUpperCase(), name);
      claim(normalizeName(name), name);
      if (iso2) claim(iso2.toUpperCase(), name);
      for (const a of alt) claim(normalizeName(a), name);
    }
    for (const [claimKey, owners] of claims) {
      if (owners.size === 1 && !byKey.has(claimKey)) byKey.set(claimKey, [...owners][0] as string);
    }
    return { mapName, byKey, canonicalNames };
  });
  cache.set(key, promise);
  // A failed fetch (offline, a corrupted deploy, ...) must not be cached forever: drop it so
  // the next prepare() tries again instead of leaving every future chart permanently blank.
  promise.catch(() => {
    if (cache.get(key) === promise) cache.delete(key);
  });
  return promise;
}

/** The map feature's canonical name for a data value (ISO code first, then normalized name). */
export function matchRegion(index: MapIndex, raw: unknown): string | null {
  if (raw === null || raw === undefined) return null;
  const text = String(raw).trim();
  if (!text || text === MISSING) return null;
  return index.byKey.get(text.toUpperCase()) ?? index.byKey.get(normalizeName(text)) ?? null;
}

/**
 * Loaded maps, read synchronously by `render`/`warnings` after `prepare` has resolved
 * (PlotEntry.prepare blocks the chart from rendering until its promise settles, so by the
 * time render/warnings run for a given spec, the map it asked for is already in here).
 */
const ready = new Map<string, MapIndex>();

export async function prepareMap(level: MapLevel, resolution: MapResolution): Promise<void> {
  const key = mapKey(level, resolution);
  if (!ready.has(key)) ready.set(key, await loadMap(level, resolution));
}

export function readyMap(level: MapLevel, resolution: MapResolution): MapIndex | undefined {
  return ready.get(mapKey(level, resolution));
}
