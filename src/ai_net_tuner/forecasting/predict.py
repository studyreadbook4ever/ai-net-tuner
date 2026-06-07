from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from ai_net_tuner.forecasting.dataset import GEANT_DATASET_NAME, GEANT_SOURCE_PAGE
from ai_net_tuner.models import Forecast, TrafficSnapshot
from ai_net_tuner.paths import models_dir, state_dir


class BaselineTrafficForecaster:
    """MVP predictor until a public forecasting model is trained.

    This keeps the rest of the system testable before Model A training. The
    intended replacement is an NHITS or PatchTST model loaded behind the same
    predict() interface.
    """

    model_name = "baseline-before-training"

    def predict(self, snapshot: TrafficSnapshot, horizon_seconds: int = 180) -> Forecast:
        burst_factor = 1.15
        pressure_factor = max(burst_factor, 1.0)
        return Forecast(
            model_name=self.model_name,
            horizon_seconds=horizon_seconds,
            dataset_name="not trained",
            dataset_source="local baseline only",
            pred_rx_mbps=round(snapshot.rx_mbps * burst_factor, 3),
            pred_tx_mbps=round(snapshot.tx_mbps * burst_factor, 3),
            pred_rx_pps=round(snapshot.rx_pps * burst_factor, 3),
            pred_tx_pps=round(snapshot.tx_pps * burst_factor, 3),
            pred_tcp_established=max(snapshot.tcp_established, int(snapshot.tcp_established * 1.05)),
            pred_tcp_time_wait=max(snapshot.tcp_time_wait, int(snapshot.tcp_time_wait * 1.10)),
            pred_tcp_syn_recv=max(snapshot.tcp_syn_recv, int(snapshot.tcp_syn_recv * 1.20)),
            pred_tcp_fin_wait2=max(snapshot.tcp_fin_wait2, int(snapshot.tcp_fin_wait2 * 1.10)),
            pred_rx_drops=snapshot.rx_drops,
            pred_rx_drops_per_sec=round(snapshot.rx_drops_per_sec * pressure_factor, 3),
            pred_tx_drops_per_sec=round(snapshot.tx_drops_per_sec * pressure_factor, 3),
            pred_rx_errors_per_sec=round(snapshot.rx_errors_per_sec * pressure_factor, 3),
            pred_tx_errors_per_sec=round(snapshot.tx_errors_per_sec * pressure_factor, 3),
            pred_tcp_active_opens_per_sec=round(snapshot.tcp_active_opens_per_sec * pressure_factor, 3),
            pred_tcp_passive_opens_per_sec=round(snapshot.tcp_passive_opens_per_sec * pressure_factor, 3),
            pred_tcp_retrans_segs_per_sec=round(snapshot.tcp_retrans_segs_per_sec * pressure_factor, 3),
            pred_tcp_syn_retrans_per_sec=round(snapshot.tcp_syn_retrans_per_sec * pressure_factor, 3),
            pred_listen_overflows_per_sec=round(snapshot.listen_overflows_per_sec * pressure_factor, 3),
            pred_listen_drops_per_sec=round(snapshot.listen_drops_per_sec * pressure_factor, 3),
            pred_udp_in_errors_per_sec=round(snapshot.udp_in_errors_per_sec * pressure_factor, 3),
            pred_udp_rcvbuf_errors_per_sec=round(snapshot.udp_rcvbuf_errors_per_sec * pressure_factor, 3),
            pred_softnet_drops_per_sec=round(snapshot.softnet_drops_per_sec * pressure_factor, 3),
            forecast_ratio=burst_factor,
        )


class GeantARForecaster:
    model_name = "geant-ridge-ar-ratio"

    def __init__(self, model_path: Path | None = None) -> None:
        self.model_path = model_path or models_dir() / "traffic_forecaster_geant.npz"
        self.history_path = state_dir() / "live_traffic_history.csv"
        self._loaded = None

    def available(self) -> bool:
        return self.model_path.exists()

    def metadata(self) -> dict:
        if not self.available():
            return {
                "dataset_name": GEANT_DATASET_NAME,
                "dataset_url": GEANT_SOURCE_PAGE,
                "status": "missing_model",
            }
        return self._load()["metadata"]

    def predict(self, snapshot: TrafficSnapshot, horizon_seconds: int = 180) -> Forecast:
        if not self.available():
            return BaselineTrafficForecaster().predict(snapshot, horizon_seconds=horizon_seconds)

        loaded = self._load()
        ratio = self._predict_ratio(snapshot, loaded)
        ratio = float(np.clip(ratio, 0.25, 4.0))
        metadata = loaded["metadata"]

        return Forecast(
            model_name=self.model_name,
            horizon_seconds=horizon_seconds,
            dataset_name=str(metadata.get("dataset_name", GEANT_DATASET_NAME)),
            dataset_source=str(metadata.get("dataset_url", GEANT_SOURCE_PAGE)),
            pred_rx_mbps=round(snapshot.rx_mbps * ratio, 3),
            pred_tx_mbps=round(snapshot.tx_mbps * ratio, 3),
            pred_rx_pps=round(snapshot.rx_pps * ratio, 3),
            pred_tx_pps=round(snapshot.tx_pps * ratio, 3),
            pred_tcp_established=max(snapshot.tcp_established, int(snapshot.tcp_established * max(ratio, 1.0))),
            pred_tcp_time_wait=max(snapshot.tcp_time_wait, int(snapshot.tcp_time_wait * max(ratio, 1.0))),
            pred_tcp_syn_recv=max(snapshot.tcp_syn_recv, int(snapshot.tcp_syn_recv * max(ratio, 1.0))),
            pred_tcp_fin_wait2=max(snapshot.tcp_fin_wait2, int(snapshot.tcp_fin_wait2 * max(ratio, 1.0))),
            pred_rx_drops=max(snapshot.rx_drops, int(snapshot.rx_drops * max(ratio, 1.0))),
            pred_rx_drops_per_sec=round(snapshot.rx_drops_per_sec * max(ratio, 1.0), 3),
            pred_tx_drops_per_sec=round(snapshot.tx_drops_per_sec * max(ratio, 1.0), 3),
            pred_rx_errors_per_sec=round(snapshot.rx_errors_per_sec * max(ratio, 1.0), 3),
            pred_tx_errors_per_sec=round(snapshot.tx_errors_per_sec * max(ratio, 1.0), 3),
            pred_tcp_active_opens_per_sec=round(snapshot.tcp_active_opens_per_sec * max(ratio, 1.0), 3),
            pred_tcp_passive_opens_per_sec=round(snapshot.tcp_passive_opens_per_sec * max(ratio, 1.0), 3),
            pred_tcp_retrans_segs_per_sec=round(snapshot.tcp_retrans_segs_per_sec * max(ratio, 1.0), 3),
            pred_tcp_syn_retrans_per_sec=round(snapshot.tcp_syn_retrans_per_sec * max(ratio, 1.0), 3),
            pred_listen_overflows_per_sec=round(snapshot.listen_overflows_per_sec * max(ratio, 1.0), 3),
            pred_listen_drops_per_sec=round(snapshot.listen_drops_per_sec * max(ratio, 1.0), 3),
            pred_udp_in_errors_per_sec=round(snapshot.udp_in_errors_per_sec * max(ratio, 1.0), 3),
            pred_udp_rcvbuf_errors_per_sec=round(snapshot.udp_rcvbuf_errors_per_sec * max(ratio, 1.0), 3),
            pred_softnet_drops_per_sec=round(snapshot.softnet_drops_per_sec * max(ratio, 1.0), 3),
            forecast_ratio=round(ratio, 6),
        )

    def record_snapshot(self, snapshot: TrafficSnapshot) -> None:
        total = max(snapshot.rx_mbps + snapshot.tx_mbps, 0.001)
        write_header = not self.history_path.exists()
        with self.history_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["timestamp", "total_mbps", "rx_mbps", "tx_mbps"])
            if write_header:
                writer.writeheader()
            writer.writerow(
                {
                    "timestamp": snapshot.timestamp,
                    "total_mbps": total,
                    "rx_mbps": snapshot.rx_mbps,
                    "tx_mbps": snapshot.tx_mbps,
                }
            )

    def _load(self) -> dict:
        if self._loaded is not None:
            return self._loaded

        data = np.load(self.model_path, allow_pickle=False)
        metadata = json.loads(str(data["metadata"][0]))
        self._loaded = {
            "coef": data["coef"].astype(np.float64),
            "lag": int(data["lag"][0]),
            "metadata": metadata,
        }
        return self._loaded

    def _history_values(self, current_total: float, lag: int) -> np.ndarray:
        values = []
        if self.history_path.exists():
            with self.history_path.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    try:
                        values.append(float(row["total_mbps"]))
                    except (KeyError, ValueError):
                        continue
        values.append(current_total)
        if len(values) < lag + 1:
            pad_value = values[0] if values else current_total
            values = [pad_value] * (lag + 1 - len(values)) + values
        return np.asarray(values[-(lag + 1) :], dtype=np.float64)

    def _predict_ratio(self, snapshot: TrafficSnapshot, loaded: dict) -> float:
        current_total = max(snapshot.rx_mbps + snapshot.tx_mbps, 0.001)
        lag = int(loaded["lag"])
        history = self._history_values(current_total, lag)
        window = history[:-1]
        current = history[-1]
        features = np.concatenate(
            [
                window / (current + 1e-9),
                np.diff(np.log1p(history)),
                [1.0],
            ]
        )
        predicted = float(features @ loaded["coef"])
        if not np.isfinite(predicted):
            return 1.0
        if loaded["metadata"].get("target") == "clipped_log_next_current_ratio":
            ratio = float(np.exp(np.clip(predicted, -2.0, 2.0)))
            return ratio
        return ratio
