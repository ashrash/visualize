"""Gaussian kernel density estimates on arrays (callers pass a capped sample, CLAUDE.md §6).

Method (the algorithm of R's density()): linear binning onto a fine regular grid, then FFT
convolution with the Gaussian kernel, zero-padded so it doesn't wrap. Cost is
O(n + M log M), so 20 groups of 100k values stay fast. The fine-grid spacing is at most
bandwidth / 8, which keeps the binning error far below plotting resolution.

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
from scipy.interpolate import RegularGridInterpolator

from viz.stats.errors import StatsInputError

Bandwidth = Literal["silverman", "scott"] | float

_MIN_FINE = 1024
_MAX_FINE = 1 << 18
_MAX_FINE_2D = 1024
_SQRT_2PI = math.sqrt(2 * math.pi)


@dataclass(frozen=True)
class KdeCurve:
    x: np.ndarray
    density: np.ndarray
    bandwidth: float
    n: int


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
    factor = 0.9 if rule == "silverman" else 1.06
    return factor * spread / math.pow(len(x), 0.2)


def _fine_size(span: float, h: float, at_least: int, cap: int) -> int:
    wanted = max(at_least, math.ceil(span / (h / 8.0)) + 1)
    return min(cap, 1 << math.ceil(math.log2(wanted)))


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


def kde(
    values: np.ndarray | Sequence[float],
    *,
    bw: Bandwidth = "silverman",
    adjust: float = 1.0,
    grid_size: int = 512,
    cut: float = 3.0,
    clip: tuple[float | None, float | None] = (None, None),
    grid: np.ndarray | None = None,
) -> KdeCurve:
    """Gaussian KDE evaluated on `grid`, or on grid_size points over [min - cut*h, max + cut*h].

    `clip` limits the evaluation range (e.g. (0, None) for non-negative data); the density is
    not renormalised. Non-finite values are ignored.
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
    m = _fine_size(hi - lo, h, max(_MIN_FINE, 2 * len(out_x)), _MAX_FINE)
    delta = (hi - lo) / (m - 1)
    density = _smooth(_linear_bin(x, lo, delta, m), delta, h) / len(x)
    fine_x = lo + delta * np.arange(m)
    out = np.interp(out_x, fine_x, np.maximum(density, 0.0))
    if clip[0] is not None:
        out[out_x < clip[0]] = 0.0
    if clip[1] is not None:
        out[out_x > clip[1]] = 0.0
    return KdeCurve(x=out_x, density=out, bandwidth=h, n=len(x))


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
    keys = sorted(set(g.tolist()), key=lambda k: (k is None, str(k) if k is not None else ""))
    warnings: list[str] = []
    members: dict[Hashable, np.ndarray] = {}
    widths: dict[Hashable, float] = {}
    pooled_h = bandwidth(v, bw) * adjust if common_bw else None
    for key in keys:
        part = v[g == key]
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
    if common_grid and members:
        lo = min(float(p.min()) - cut * widths[k] for k, p in members.items())
        hi = max(float(p.max()) + cut * widths[k] for k, p in members.items())
        if clip[0] is not None:
            lo = max(lo, float(clip[0]))
        if clip[1] is not None:
            hi = min(hi, float(clip[1]))
        grid = np.linspace(lo, hi, grid_size)
    curves = [
        GroupCurve(
            key=key,
            n=len(part),
            curve=kde(part, bw=widths[key], grid_size=grid_size, cut=cut, clip=clip, grid=grid),
        )
        for key, part in members.items()
    ]
    return GroupedKde(curves=curves, warnings=warnings)


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
    mx = _fine_size(hi_x - lo_x, hx, max(256, 2 * grid_size[0]), _MAX_FINE_2D)
    my = _fine_size(hi_y - lo_y, hy, max(256, 2 * grid_size[1]), _MAX_FINE_2D)
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
    grid = _smooth(_smooth(grid, dx, hx, axis=1), dy, hy, axis=0) / len(x)
    fine_x = lo_x + dx * np.arange(mx)
    fine_y = lo_y + dy * np.arange(my)
    interpolate = RegularGridInterpolator((fine_y, fine_x), np.maximum(grid, 0.0))
    yy, xx = np.meshgrid(ys, xs, indexing="ij")
    density = interpolate(np.stack([yy.ravel(), xx.ravel()], axis=1)).reshape(len(ys), len(xs))
    return Kde2d(xs=xs, ys=ys, density=density, bw_x=hx, bw_y=hy, n=len(x))
