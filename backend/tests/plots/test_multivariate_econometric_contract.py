"""The frontend registry entries (charts/plots/{multivariate,econometric}) and the server modules
agree: every param the config panel stores is accepted (params models forbid unknown keys), and
channel names, required-ness and multiplicity match.

FRONTEND is the entries' `defaults.params` and `encodingsSchema` as (name, required, multiple),
dumped from the TypeScript registry. Update both sides together.
"""

from __future__ import annotations

from typing import Any

import pytest

import viz.plots as plots
from viz.contracts import PlotType

FRONTEND: dict[str, dict[str, Any]] = {
    "radar": {
        "params": {"agg": "mean", "scale": "column_range", "maxGroups": 12, "fill": True},
        "channels": [["y", True, True], ["color", False, False]],
    },
    "parallel_coordinates": {
        "params": {"maxLines": 1000, "opacity": 0.35, "smooth": False},
        "channels": [["y", True, True], ["color", False, False]],
    },
    "pca_biplot": {
        "params": {
            "standardize": True,
            "xComponent": 1,
            "yComponent": 2,
            "maxPoints": 2000,
            "showPoints": True,
            "showLoadings": True,
            "labelPoints": False,
        },
        "channels": [["y", True, True], ["color", False, False], ["label", False, False]],
    },
    "factor_loading": {
        "params": {
            "method": "fa",
            "factors": 2,
            "rotation": "varimax",
            "display": "heatmap",
            "showValues": True,
        },
        "channels": [["y", True, True]],
    },
    "cluster": {
        "params": {
            "algorithm": "kmeans",
            "k": 3,
            "eps": 0.5,
            "minSamples": 5,
            "standardize": True,
            "diagnostics": True,
            "maxPoints": 2000,
            "view": "clusters",
            "showCentroids": True,
        },
        "channels": [["y", True, True], ["label", False, False]],
    },
    "dendrogram": {
        "params": {
            "method": "ward",
            "metric": "euclidean",
            "standardize": True,
            "clusters": 0,
            "maxLeaves": 40,
            "maxRows": 2000,
            "orientation": "top",
        },
        "channels": [["y", True, True], ["label", False, False]],
    },
    "andrews_curves": {
        "params": {"maxCurves": 200, "points": 100, "scale": "zscore", "opacity": 0.4},
        "channels": [["y", True, True], ["color", False, False]],
    },
    "kaplan_meier": {
        "params": {
            "ciLevel": 0.95,
            "ciType": "log-log",
            "maxGroups": 10,
            "showCi": True,
            "showCensored": True,
            "showMedian": False,
        },
        "channels": [["time", True, False], ["event", True, False], ["color", False, False]],
    },
    "forest": {
        "params": {
            "measure": "difference",
            "pooled": "random",
            "ciLevel": 0.95,
            "maxRows": 200,
            "sort": "data",
            "nullLine": True,
        },
        "channels": [
            ["label", True, False],
            ["estimate", True, False],
            ["lower", False, False],
            ["upper", False, False],
            ["se", False, False],
            ["color", False, False],
        ],
    },
    "roc": {
        "params": {"positive": "", "ciLevel": 0.95, "showDiagonal": True, "showBest": True},
        "channels": [["truth", True, False], ["score", True, True]],
    },
    "precision_recall": {
        "params": {"positive": "", "showBaseline": True, "showBest": True},
        "channels": [["truth", True, False], ["score", True, True]],
    },
    "lorenz": {
        "params": {"points": 200, "maxGroups": 10, "showEquality": True, "fill": True},
        "channels": [["y", True, False], ["color", False, False]],
    },
    "quantile": {
        "params": {"maxPoints": 1000, "maxGroups": 10, "showQuartiles": True, "connect": False},
        "channels": [["y", True, False], ["color", False, False]],
    },
    "marginal_effects": {
        "params": {
            "model": "logit",
            "at": "overall",
            "discrete": True,
            "robust": False,
            "ciLevel": 0.95,
            "sort": "model",
        },
        "channels": [["y", True, False], ["x", True, True], ["cluster", False, False]],
    },
    "coefficient": {
        "params": {
            "model": "ols",
            "standardize": False,
            "robust": False,
            "ciLevel": 0.95,
            "sort": "model",
            "showIntercept": False,
            "exponentiate": False,
        },
        "channels": [["y", True, False], ["x", True, True], ["cluster", False, False]],
    },
    "impulse_response": {
        # `impulse` is a column param with no default; the panel stores a field name.
        "params": {
            "lags": 0,
            "periods": 12,
            "orthogonalized": True,
            "ciLevel": 0.95,
            "impulse": "gdp",
        },
        "channels": [["y", True, True], ["time", True, False]],
    },
    "event_study": {
        "params": {
            "windowStart": -5,
            "windowEnd": 5,
            "reference": -1,
            "fixedEffects": "unit_time",
            "binEndpoints": True,
            "cluster": True,
            "ciLevel": 0.95,
        },
        "channels": [
            ["y", True, False],
            ["unit", True, False],
            ["time", True, False],
            ["treatTime", False, False],
            ["eventTime", False, False],
            ["x", False, True],
        ],
    },
}


@pytest.mark.parametrize("key", sorted(FRONTEND))
def test_frontend_entry_matches_server_module(key: str) -> None:
    definition = plots.get_plot(PlotType(key))
    assert definition is not None
    expected = FRONTEND[key]
    parsed = definition.params.model_validate(expected["params"])
    # Every default round-trips to the same value (no silent coercion).
    dumped = parsed.model_dump(by_alias=True)
    for name, value in expected["params"].items():
        assert dumped[name] == value, name
    channels = [[c.name, c.min > 0, c.max is None or c.max > 1] for c in definition.channels]
    assert channels == expected["channels"]
