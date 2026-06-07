from __future__ import annotations

import urllib.request
from pathlib import Path

import pandas as pd

from ai_net_tuner.paths import datasets_dir


GEANT_DATASET_NAME = "GÉANT Backbone Network traffic matrix dataset"
GEANT_DATASET_SOURCE = "duchuyle108/SDN-TMprediction dataset/geant-flat-tms.csv"
GEANT_DATASET_URL = (
    "https://raw.githubusercontent.com/duchuyle108/SDN-TMprediction/"
    "main/dataset/geant-flat-tms.csv"
)
GEANT_SOURCE_PAGE = "https://github.com/duchuyle108/SDN-TMprediction"
GEANT_INTERVAL_MINUTES = 15


def geant_csv_path() -> Path:
    return datasets_dir() / "geant" / "geant-flat-tms.csv"


def download_geant_dataset(force: bool = False) -> Path:
    path = geant_csv_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 1_000_000 and not force:
        return path

    print(f"Downloading {GEANT_DATASET_NAME}")
    print(f"Source: {GEANT_SOURCE_PAGE}")
    urllib.request.urlretrieve(GEANT_DATASET_URL, path)
    return path


def load_geant_aggregate_series(path: Path) -> pd.Series:
    # The flattened GÉANT matrix CSV has no semantic header row in practice.
    # Column 0 is the timestamp and all remaining columns are OD traffic values.
    frame = pd.read_csv(path, header=None)
    timestamps = pd.to_datetime(frame.iloc[:, 0], format="%Y-%m-%d-%H-%M", errors="coerce")
    values = frame.iloc[:, 1:].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    aggregate = values.sum(axis=1)
    aggregate.index = timestamps
    return aggregate.astype(float)
