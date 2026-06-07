from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from ai_net_tuner.forecasting.dataset import (
    GEANT_DATASET_NAME,
    GEANT_DATASET_SOURCE,
    GEANT_INTERVAL_MINUTES,
    GEANT_SOURCE_PAGE,
    download_geant_dataset,
    geant_csv_path,
    load_geant_aggregate_series,
)
from ai_net_tuner.paths import models_dir


DEFAULT_MODEL_PATH = models_dir() / "traffic_forecaster_geant.npz"


def _build_ratio_dataset(values: np.ndarray, lag: int) -> tuple[np.ndarray, np.ndarray]:
    x_rows = []
    y_rows = []
    eps = 1e-9
    for idx in range(lag, len(values) - 1):
        window = values[idx - lag : idx]
        current = values[idx]
        nxt = values[idx + 1]
        if current <= 0 or np.any(window <= 0):
            continue
        # Shape features: previous traffic values normalized by current value.
        # This lets the public-dataset model transfer to local live traffic scale.
        features = np.concatenate(
            [
                window / (current + eps),
                np.diff(np.log1p(np.concatenate([window, [current]]))),
                [1.0],
            ]
        )
        ratio = nxt / (current + eps)
        log_ratio = np.log1p(nxt) - np.log1p(current)
        if np.isfinite(ratio) and np.isfinite(log_ratio):
            x_rows.append(features)
            y_rows.append(float(np.clip(log_ratio, -2.0, 2.0)))
    return np.asarray(x_rows, dtype=np.float64), np.asarray(y_rows, dtype=np.float64)


def train_geant_ar_model(
    *,
    dataset_path: Path,
    model_path: Path,
    lag: int = 10,
    ridge_alpha: float = 0.05,
) -> dict:
    series = load_geant_aggregate_series(dataset_path)
    values = series.to_numpy(dtype=np.float64)
    x, y = _build_ratio_dataset(values, lag)
    if len(x) < 100:
        raise RuntimeError("not enough GÉANT samples to train traffic forecaster")

    split = int(len(x) * 0.8)
    x_train, x_test = x[:split], x[split:]
    y_train, y_test = y[:split], y[split:]

    regularizer = ridge_alpha * np.eye(x_train.shape[1], dtype=np.float64)
    coef = np.linalg.solve(x_train.T @ x_train + regularizer, x_train.T @ y_train)
    pred_log = np.clip(x_test @ coef, -2.0, 2.0)
    rmse = float(np.sqrt(np.mean((pred_log - y_test) ** 2)))
    mae = float(np.mean(np.abs(pred_log - y_test)))
    ratio_rmse = float(np.sqrt(np.mean((np.exp(pred_log) - np.exp(y_test)) ** 2)))

    metadata = {
        "model_type": "ridge_autoregressive_ratio_forecaster",
        "target": "clipped_log_next_current_ratio",
        "dataset_name": GEANT_DATASET_NAME,
        "dataset_source": GEANT_DATASET_SOURCE,
        "dataset_url": GEANT_SOURCE_PAGE,
        "dataset_interval_minutes": GEANT_INTERVAL_MINUTES,
        "samples": int(len(values)),
        "training_rows": int(len(x_train)),
        "test_rows": int(len(x_test)),
        "lag": int(lag),
        "ridge_alpha": float(ridge_alpha),
        "test_rmse_log_ratio": rmse,
        "test_mae_log_ratio": mae,
        "test_rmse_ratio": ratio_rmse,
    }

    model_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        model_path,
        coef=coef,
        lag=np.asarray([lag], dtype=np.int64),
        metadata=np.asarray([json.dumps(metadata, ensure_ascii=False)]),
    )
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true", help="download GÉANT CSV before training")
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--dataset", default=str(geant_csv_path()))
    parser.add_argument("--output", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--lag", type=int, default=10)
    parser.add_argument("--ridge-alpha", type=float, default=0.05)
    args = parser.parse_args(argv)

    dataset_path = download_geant_dataset(force=args.force_download) if args.download else Path(args.dataset)
    metadata = train_geant_ar_model(
        dataset_path=dataset_path,
        model_path=Path(args.output),
        lag=args.lag,
        ridge_alpha=args.ridge_alpha,
    )
    print("Traffic forecasting model trained.")
    print(f"Dataset: {metadata['dataset_name']}")
    print(f"Source: {metadata['dataset_url']}")
    print(f"Samples: {metadata['samples']}")
    print(f"Test RMSE on next-step ratio: {metadata['test_rmse_ratio']:.6f}")
    print(f"Saved: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
