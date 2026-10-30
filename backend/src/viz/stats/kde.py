"""Gaussian kernel density estimates on arrays (callers pass a capped sample, CLAUDE.md §6).

Method (the algorithm of R's density()): linear binning onto a fine regular grid, then FFT
convolution with the Gaussian kernel, zero-padded so it doesn't wrap. Cost is
O(n + M log M), so 20 groups of 100k values stay fast. The fine grid is chosen with spacing
at most bandwidth / 8, which keeps the binning error far below plotting resolution.

The binned curve is then interpolated at the output points, which is right only when the
output grid resolves the kernel. So whenever the output spacing exceeds h/2, or the range is
too wide for the fine-grid cap (skewed data: lognormal, heavy tails), the output is
computed exactly instead, as the KDE's mass in each output point's cell divided by the cell
width (method "cell_average"). If the fine grid exists, the cells integrate its density
(a cumulative sum in 1-D, a summed-area table in 2-D); if the range is too wide for it, the
cells are computed exactly (a windowed smoothed CDF over sorted data in 1-D, separable
per-axis cell probabilities in 2-D). On a fine grid that equals the point density; on a
coarse grid it still integrates to the KDE's mass, where point values would miss narrow
peaks. Groups sharing one grid (violins, ridgelines) all use the same method. Data
spanning more than 1000 bandwidths gets a log-scale warning.
direct_density() gives exact point values when those are wanted.

Bandwidth rules (R's, both robust to outliers via the IQR):
- "silverman" (default): 0.9 * min(sd, IQR / 1.34) * n^(-1/5)   (R bw.nrd0)
- "scott":               1.06 * min(sd, IQR / 1.34) * n^(-1/5)  (R bw.nrd)
If IQR is 0 the standard deviation is used alone. A number is taken as the bandwidth itself.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from scipy import special
from scipy.interpolate import RegularGridInterpolator

from viz.stats.errors import StatsInputError
from viz.stats.results import dedupe

Bandwidth = Literal["silverman", "scott"] | float

_MIN_FINE = 1024
_MAX_FINE = 1 << 18
_MAX_FINE_2D = 1024
_SQRT_2PI = math.sqrt(2 * math.pi)
_TRUNCATE = 8.0  # kernel window in bandwidths for exact evaluation (tail < 1e-15)
_BLOCK = 4_000_000  # kernel evaluations per numpy block
WIDE_RANGE_WARNING = "The data range is very wide relative to the bandwidth; consider a log scale."


@dataclass(frozen=True)
class KdeCurve:
    x: np.ndarray
    density: np.ndarray
    bandwidth: float
    n: int
    # "binned" (FFT on a fine grid) or "cell_average" (exact mass per cell / width).
    method: str = "binned"
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GroupCurve:
    key: Hashable
    n: int
    curve: KdeCurve


@dataclass(frozen=True)
class GroupedKde:
    curves: list[GroupCurve]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Kde2d:
    xs: np.ndarray
    ys: np.ndarray
    # density[j, i] is the density at (xs[i], ys[j]).
    density: np.ndarray
    bw_x: float
    bw_y: float
    n: int
    method: str = "binned"
    warnings: list[str] = field(default_factory=list)


def _finite(values: np.ndarray | Sequence[float], what: str = "values") -> np.ndarray:
    x = np.asarray(values, dtype=float).ravel()
    x = x[np.isfinite(x)]
    if len(x) < 2:
        raise StatsInputError(f"A density estimate needs at least 2 {what}.")
    return x


def bandwidth(values: np.ndarray | Sequence[float], rule: Bandwidth = "silverman") -> float:
    """Kernel standard deviation by rule (see module docstring), or `rule` itself if a number."""
    if not isinstance(rule, str):
        h = float(rule)
        if not (h > 0 and math.isfinite(h)):
            raise StatsInputError("The bandwidth must be a positive number.")
        return h
    if rule not in ("silverman", "scott"):
        raise StatsInputError(f"Unknown bandwidth rule '{rule}'.")
    x = _finite(values)
    sd = float(np.std(x, ddof=1))
    q1, q3 = np.quantile(x, [0.25, 0.75])
    spread = min(sd, float(q3 - q1) / 1.34) or sd
    if spread == 0:
        raise StatsInputError("All values are equal, so there is no density to estimate.")
    if not math.isfinite(spread):
        raise StatsInputError("The values are too large for a density estimate.")
    factor = 0.9 if rule == "silverman" else 1.06
    return factor * spread / math.pow(len(x), 0.2)


def _fine_size(span: float, h: float, at_least: int, cap: int) -> tuple[int, bool]:
    """Grid points (a power of two) for spacing <= h/8, and whether the cap prevented that."""
    needed = math.ceil(span / (h / 8.0)) + 1
    size = 1 << math.ceil(math.log2(max(at_least, needed)))
    return min(cap, size), needed > cap


def _linear_bin(x: np.ndarray, lo: float, delta: float, m: int) -> np.ndarray:
    """Spread each value over its two neighbouring grid points (weights sum to 1)."""
    t = (x - lo) / delta
    i = np.clip(np.floor(t).astype(np.int64), 0, m - 2)
    w = np.clip(t - i, 0.0, 1.0)
    return np.bincount(i, 1.0 - w, minlength=m) + np.bincount(i + 1, w, minlength=m)


def _kernel_fft(m: int, delta: float, h: float) -> np.ndarray:
    """rFFT of the Gaussian kernel sampled on a circular grid of length 2m."""
    k = np.arange(2 * m)
    dist = np.where(k <= m, k, k - 2 * m) * delta
    kernel = np.exp(-0.5 * (dist / h) ** 2) / (h * _SQRT_2PI)
    return np.fft.rfft(kernel)


def _smooth(counts: np.ndarray, delta: float, h: float, axis: int = -1) -> np.ndarray:
    """Convolve binned counts with the kernel along `axis` (zero-padded, no wraparound)."""
    m = counts.shape[axis]
    spectrum = np.fft.rfft(counts, n=2 * m, axis=axis)
    shape = [1] * counts.ndim
    shape[axis] = -1
    smoothed = np.fft.irfft(spectrum * _kernel_fft(m, delta, h).reshape(shape), n=2 * m, axis=axis)
    return np.take(smoothed, np.arange(m), axis=axis)


def direct_density(x: np.ndarray, at: np.ndarray, h: float) -> np.ndarray:
    """Exact Gaussian KDE at `at`: windowed sums over sorted data (|x - a| <= 8h), blocked."""
    data = np.sort(x)
    order = np.argsort(at, kind="stable")
    points = at[order]
    lo = np.searchsorted(data, points - _TRUNCATE * h, side="left")
    hi = np.searchsorted(data, points + _TRUNCATE * h, side="right")
    out = np.zeros(len(points))
    start = 0
    while start < len(points):
        stop = start + 1
        # Grow the block while (points x window) stays small; windows only move right.
        while stop < len(points) and (stop + 1 - start) * (hi[stop] - lo[start]) <= _BLOCK:
            stop += 1
        window = data[lo[start] : hi[stop - 1]]
        if len(window):
            u = (points[start:stop, None] - window[None, :]) / h
            out[start:stop] = np.exp(-0.5 * u * u).sum(axis=1)
        start = stop
    density = np.empty(len(points))
    density[order] = out / (len(x) * h * _SQRT_2PI)
    return density


def _cells(points: np.ndarray, h: float) -> tuple[np.ndarray, np.ndarray]:
    """Cell [a, b) around each sorted point: midpoints between neighbours, mirrored at ends."""
    if len(points) == 1:
        return points - h / 2, points + h / 2
    mid = (points[1:] + points[:-1]) / 2
    a = np.concatenate([[points[0] - (mid[0] - points[0])], mid])
    b = np.concatenate([mid, [points[-1] + (points[-1] - mid[-1])]])
    return a, b


def _smoothed_cdf(data: np.ndarray, edges: np.ndarray, h: float) -> np.ndarray:
    """(1/n) sum_k Phi((e - x_k) / h) at sorted edges, from sorted data (windowed, exact)."""
    lo = np.searchsorted(data, edges - _TRUNCATE * h, side="left")
    hi = np.searchsorted(data, edges + _TRUNCATE * h, side="right")
    out = lo.astype(float)  # data far left of an edge contributes Phi = 1
    start = 0
    while start < len(edges):
        stop = start + 1
        while stop < len(edges) and (stop + 1 - start) * (hi[stop] - lo[start]) <= _BLOCK:
            stop += 1
        window = data[lo[start] : hi[stop - 1]]
        if len(window):
            u = (edges[start:stop, None] - window[None, :]) / h
            inside = (window[None, :] >= (edges[start:stop, None] - _TRUNCATE * h)) & (
                window[None, :] <= (edges[start:stop, None] + _TRUNCATE * h)
            )
            # Within each edge's own window add Phi; window points left of it count as 1 already.
            out[start:stop] += np.where(inside, special.ndtr(u), 0.0).sum(axis=1)
        start = stop
    return out / len(data)


def cell_density(x: np.ndarray, at: np.ndarray, h: float) -> np.ndarray:
    """Exact KDE mass in each point's cell divided by the cell width (any point spacing).

    On a grid finer than the bandwidth this equals the point density; on a coarse grid it
    keeps the curve's total mass (point values there would miss narrow peaks entirely).
    Repeated points share one cell (a zero-width cell would be undefined).
    """
    data = np.sort(x)
    points, inverse = np.unique(np.asarray(at, dtype=float), return_inverse=True)
    a, b = _cells(points, h)
    edges = np.concatenate([a, b[-1:]])
    cdf = _smoothed_cdf(data, edges, h)
    density: np.ndarray = ((cdf[1:] - cdf[:-1]) / (b - a))[inverse.ravel()]
    return density


def coarse_grid(points: np.ndarray, h: float) -> bool:
    """True when some gap between neighbouring output points exceeds h/2.

    A curve sampled that sparsely misses the shape of a kernel bump, so its point values
    don't integrate to the KDE's mass; cell averages do.
    """
    unique = np.unique(points)
    return len(unique) > 1 and float(np.max(np.diff(unique))) > h / 2


KdeMethod = Literal["auto", "binned", "cell_average"]

# Data ranges wider than this many bandwidths can't be shown well on a linear axis.
_WIDE_RATIO = 1000.0


def _wide(span: float, h: float) -> bool:
    return span / h > _WIDE_RATIO


def kde(
    values: np.ndarray | Sequence[float],
    *,
    bw: Bandwidth = "silverman",
    adjust: float = 1.0,
    grid_size: int = 512,
    cut: float = 3.0,
    clip: tuple[float | None, float | None] = (None, None),
    grid: np.ndarray | None = None,
    method: KdeMethod = "auto",
) -> KdeCurve:
    """Gaussian KDE evaluated on `grid`, or on grid_size points over [min - cut*h, max + cut*h].

    `clip` limits the evaluation range (e.g. (0, None) for non-negative data); the density is
    not renormalised. Non-finite values are ignored.

    method="auto" uses cell averages when the output spacing exceeds h/2 or the fine grid
    can't reach h/8, and the binned FFT otherwise (the two agree on fine output grids).
    "binned" is overridden when the fine grid can't be built.
    """
    x = _finite(values)
    if adjust <= 0:
        raise StatsInputError("The bandwidth adjustment must be positive.")
    h = bandwidth(x, bw) * adjust
    lo_out = float(x.min()) - cut * h
    hi_out = float(x.max()) + cut * h
    if clip[0] is not None:
        lo_out = max(lo_out, float(clip[0]))
    if clip[1] is not None:
        hi_out = min(hi_out, float(clip[1]))
    out_x = np.linspace(lo_out, hi_out, grid_size) if grid is None else np.asarray(grid, float)

    lo = min(float(x.min()), float(out_x.min())) - 4 * h
    hi = max(float(x.max()), float(out_x.max())) + 4 * h
    m, capped = _fine_size(hi - lo, h, max(_MIN_FINE, 2 * len(out_x)), _MAX_FINE)
    use_cells = capped or method == "cell_average" or (method == "auto" and coarse_grid(out_x, h))
    warnings = [WIDE_RANGE_WARNING] if _wide(float(np.ptp(x)), h) else []
    if use_cells and capped:
        out = cell_density(x, out_x, h)  # exact: the fine grid can't resolve h
        method_used = "cell_average"
    else:
        delta = (hi - lo) / (m - 1)
        density = np.maximum(_smooth(_linear_bin(x, lo, delta, m), delta, h) / len(x), 0.0)
        fine_x = lo + delta * np.arange(m)
        if use_cells:
            # Cell averages from the fine (h/8) density: integrate it over each output cell.
            cumulative = np.concatenate([[0.0], np.cumsum((density[1:] + density[:-1]) / 2)])
            points, inverse = np.unique(out_x, return_inverse=True)
            a, b = _cells(points, h)
            mass = np.interp(b, fine_x, cumulative) - np.interp(a, fine_x, cumulative)
            out = (mass * delta / (b - a))[inverse.ravel()]
            method_used = "cell_average"
        else:
            out = np.interp(out_x, fine_x, density)
            method_used = "binned"
    if clip[0] is not None:
        out[out_x < clip[0]] = 0.0
    if clip[1] is not None:
        out[out_x > clip[1]] = 0.0
    return KdeCurve(
        x=out_x, density=out, bandwidth=h, n=len(x), method=method_used, warnings=warnings
    )


def kde_by_group(
    values: np.ndarray | Sequence[float],
    groups: Sequence[Hashable] | np.ndarray,
    *,
    bw: Bandwidth = "silverman",
    adjust: float = 1.0,
    grid_size: int = 512,
    cut: float = 3.0,
    clip: tuple[float | None, float | None] = (None, None),
    common_grid: bool = True,
    common_bw: bool = False,
    min_n: int = 2,
) -> GroupedKde:
    """One density per group. Groups too small or constant are skipped with a warning.

    common_grid evaluates every group on one grid (comparable violins/ridgelines);
    common_bw uses the pooled data's bandwidth for every group.
    """
    v = np.asarray(values, dtype=float).ravel()
    g = np.asarray(groups, dtype=object).ravel()
    if len(v) != len(g):
        raise ValueError("values and groups must have the same length")
    keep = np.isfinite(v)
    v, g = v[keep], g[keep]
    # Factorize once (keys may mix None and values, which np.unique can't sort), then split
    # the values by group code with one stable argsort.
    index: dict[Hashable, int] = {}
    codes = np.fromiter((index.setdefault(k, len(index)) for k in g.tolist()), np.int64, len(g))
    uniques = list(index)
    order = np.argsort(codes, kind="stable")
    bounds = np.searchsorted(codes[order], np.arange(len(uniques) + 1))
    parts = {uniques[c]: v[order[bounds[c] : bounds[c + 1]]] for c in range(len(uniques))}
    keys = sorted(parts, key=lambda k: (k is None, str(k) if k is not None else ""))
    warnings: list[str] = []
    members: dict[Hashable, np.ndarray] = {}
    widths: dict[Hashable, float] = {}
    pooled_h = bandwidth(v, bw) * adjust if common_bw else None
    for key in keys:
        part = parts[key]
        label = "null" if key is None else str(key)
        if len(part) < max(min_n, 2):
            warnings.append(f"Group '{label}' has fewer than {max(min_n, 2)} values; no density.")
            continue
        if np.ptp(part) == 0 and not isinstance(bw, int | float):
            warnings.append(f"Group '{label}' has only one distinct value; no density.")
            continue
        members[key] = part
        widths[key] = pooled_h if pooled_h is not None else bandwidth(part, bw) * adjust
    grid = None
    method: KdeMethod = "auto"
    if common_grid and members:
        lo = min(float(p.min()) - cut * widths[k] for k, p in members.items())
        hi = max(float(p.max()) + cut * widths[k] for k, p in members.items())
        if clip[0] is not None:
            lo = max(lo, float(clip[0]))
        if clip[1] is not None:
            hi = min(hi, float(clip[1]))
        grid = np.linspace(lo, hi, grid_size)
        # One rule for every group on the shared grid: cell averages if it's too coarse for
        # the narrowest bandwidth, so all curves on a violin/ridgeline are comparable.
        method = "cell_average" if coarse_grid(grid, min(widths.values())) else "binned"
    curves = [
        GroupCurve(
            key=key,
            n=len(part),
            curve=kde(
                part,
                bw=widths[key],
                grid_size=grid_size,
                cut=cut,
                clip=clip,
                grid=grid,
                method=method,
            ),
        )
        for key, part in members.items()
    ]
    for c in curves:
        warnings.extend(c.curve.warnings)
    return GroupedKde(curves=curves, warnings=dedupe(warnings))


def kde2d(
    x_values: np.ndarray | Sequence[float],
    y_values: np.ndarray | Sequence[float],
    *,
    bw: Bandwidth | tuple[Bandwidth, Bandwidth] = "silverman",
    adjust: float = 1.0,
    grid_size: tuple[int, int] = (100, 100),
    cut: float = 3.0,
) -> Kde2d:
    """2-D Gaussian product-kernel KDE (per-axis bandwidths, as MASS::kde2d) on a grid."""
    x = np.asarray(x_values, dtype=float).ravel()
    y = np.asarray(y_values, dtype=float).ravel()
    if len(x) != len(y):
        raise ValueError("x and y must have the same length")
    keep = np.isfinite(x) & np.isfinite(y)
    x, y = x[keep], y[keep]
    if len(x) < 2:
        raise StatsInputError("A density estimate needs at least 2 points.")
    bw_x, bw_y = bw if isinstance(bw, tuple) else (bw, bw)
    hx = bandwidth(x, bw_x) * adjust
    hy = bandwidth(y, bw_y) * adjust
    xs = np.linspace(x.min() - cut * hx, x.max() + cut * hx, grid_size[0])
    ys = np.linspace(y.min() - cut * hy, y.max() + cut * hy, grid_size[1])

    lo_x, hi_x = float(xs[0]) - 4 * hx, float(xs[-1]) + 4 * hx
    lo_y, hi_y = float(ys[0]) - 4 * hy, float(ys[-1]) + 4 * hy
    mx, capped_x = _fine_size(hi_x - lo_x, hx, max(256, 2 * grid_size[0]), _MAX_FINE_2D)
    my, capped_y = _fine_size(hi_y - lo_y, hy, max(256, 2 * grid_size[1]), _MAX_FINE_2D)
    wide = _wide(float(np.ptp(x)), hx) or _wide(float(np.ptp(y)), hy)
    warnings = [WIDE_RANGE_WARNING] if wide else []
    if capped_x or capped_y:  # exact: the fine grid can't resolve the bandwidth
        density = _cell_density_2d(x, y, xs, ys, hx, hy)
        return Kde2d(xs, ys, density, hx, hy, len(x), "cell_average", warnings)
    use_cells = coarse_grid(xs, hx) or coarse_grid(ys, hy)

    dx, dy = (hi_x - lo_x) / (mx - 1), (hi_y - lo_y) / (my - 1)
    tx, ty = (x - lo_x) / dx, (y - lo_y) / dy
    ix = np.clip(np.floor(tx).astype(np.int64), 0, mx - 2)
    iy = np.clip(np.floor(ty).astype(np.int64), 0, my - 2)
    wx, wy = np.clip(tx - ix, 0, 1), np.clip(ty - iy, 0, 1)
    counts = np.zeros(my * mx)
    for ddy, weight_y in ((0, 1 - wy), (1, wy)):
        for ddx, weight_x in ((0, 1 - wx), (1, wx)):
            cells = (iy + ddy) * mx + (ix + ddx)
            counts += np.bincount(cells, weight_y * weight_x, minlength=my * mx)
    grid = counts.reshape(my, mx)
    grid = np.maximum(_smooth(_smooth(grid, dx, hx, axis=1), dy, hy, axis=0) / len(x), 0.0)
    fine_x = lo_x + dx * np.arange(mx)
    fine_y = lo_y + dy * np.arange(my)
    if use_cells:
        density = _cells_from_fine_2d(grid, fine_x, fine_y, xs, ys, hx, hy)
        return Kde2d(xs, ys, density, hx, hy, len(x), "cell_average", warnings)
    interpolate = RegularGridInterpolator((fine_y, fine_x), grid)
    yy, xx = np.meshgrid(ys, xs, indexing="ij")
    density = interpolate(np.stack([yy.ravel(), xx.ravel()], axis=1)).reshape(len(ys), len(xs))
    return Kde2d(xs, ys, density, hx, hy, len(x), "binned", warnings)


def _cells_from_fine_2d(
    fine: np.ndarray,
    fine_x: np.ndarray,
    fine_y: np.ndarray,
    xs: np.ndarray,
    ys: np.ndarray,
    hx: float,
    hy: float,
) -> np.ndarray:
    """Cell averages of a fine-grid density: a summed-area table, read at the cell corners."""
    dx, dy = fine_x[1] - fine_x[0], fine_y[1] - fine_y[0]
    # Trapezoid cumulative integral along each axis (row 0 / column 0 are zero).
    along_x = np.concatenate(
        [np.zeros((fine.shape[0], 1)), np.cumsum((fine[:, 1:] + fine[:, :-1]) / 2, axis=1)],
        axis=1,
    )
    table = np.concatenate(
        [np.zeros((1, fine.shape[1])), np.cumsum((along_x[1:] + along_x[:-1]) / 2, axis=0)],
        axis=0,
    ) * (dx * dy)
    corners = RegularGridInterpolator((fine_y, fine_x), table)
    ax, bx = _cells(xs, hx)
    ay, by = _cells(ys, hy)
    cx = np.clip(np.concatenate([ax, bx[-1:]]), fine_x[0], fine_x[-1])
    cy = np.clip(np.concatenate([ay, by[-1:]]), fine_y[0], fine_y[-1])
    yy, xx = np.meshgrid(cy, cx, indexing="ij")
    s = corners(np.stack([yy.ravel(), xx.ravel()], axis=1)).reshape(len(cy), len(cx))
    mass = s[1:, 1:] - s[:-1, 1:] - s[1:, :-1] + s[:-1, :-1]
    area: np.ndarray = np.outer(by - ay, bx - ax)
    density: np.ndarray = mass / area
    return density


def _cell_density_2d(
    x: np.ndarray, y: np.ndarray, xs: np.ndarray, ys: np.ndarray, hx: float, hy: float
) -> np.ndarray:
    """Exact product-kernel mass per grid cell over cell area (cells centred on xs, ys).

    Mass of point k in cell (i, j) is Px[i, k] * Py[j, k], with Px the kernel's probability
    in the x cell, so the grid is Py @ Px.T, accumulated over chunks of the data.
    """
    ax, bx = _cells(xs, hx)
    ay, by = _cells(ys, hy)
    mass = np.zeros((len(ys), len(xs)))
    chunk = max(1, _BLOCK // max(len(xs), len(ys)))
    for start in range(0, len(x), chunk):
        xk, yk = x[None, start : start + chunk], y[None, start : start + chunk]
        px = special.ndtr((bx[:, None] - xk) / hx) - special.ndtr((ax[:, None] - xk) / hx)
        py = special.ndtr((by[:, None] - yk) / hy) - special.ndtr((ay[:, None] - yk) / hy)
        mass += py @ px.T
    area: np.ndarray = np.outer(by - ay, bx - ax)
    density: np.ndarray = mass / (len(x) * area)
    return density
