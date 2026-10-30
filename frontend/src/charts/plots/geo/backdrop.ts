import type { Obj } from "../../core/series";
import type { ChartTheme } from "../../core/types";
import { type MapResolution, prepareMap, readyMap } from "./assets/maps";

/** The faint world backdrop behind points/circles that aren't themselves region-colored. */
export async function prepareBackdrop(resolution: MapResolution): Promise<void> {
  await prepareMap("country", resolution);
}

export function backdropMapName(resolution: MapResolution): string | undefined {
  return readyMap("country", resolution)?.mapName;
}

/** A silent (non-interactive-fill) `geo` component: countries drawn faintly, points on top. */
export function geoBackdrop(mapName: string | undefined, theme: ChartTheme): Obj {
  return {
    map: mapName ?? "world",
    roam: true,
    itemStyle: { areaColor: theme.grid, borderColor: theme.border },
    emphasis: { itemStyle: { areaColor: theme.grid } },
    label: { show: false },
  };
}
