from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from ai_net_tuner.models import ReviewedProposal, utc_like_now
from ai_net_tuner.paths import state_dir


HEADERS = [
    "timestamp",
    "cycle_id",
    "proposal_id",
    "key",
    "current",
    "proposed",
    "short_ko",
    "risk_level",
    "warn_ko",
    "model_a",
    "model_a_dataset",
    "model_a_dataset_source",
    "pred_rx_mbps",
    "pred_tx_mbps",
    "pred_rx_pps",
    "pred_tx_pps",
    "pred_tcp_syn_recv",
    "pred_tcp_time_wait",
    "pred_tcp_retrans_segs_per_sec",
    "pred_listen_overflows_per_sec",
    "pred_listen_drops_per_sec",
    "pred_rx_drops_per_sec",
    "pred_softnet_drops_per_sec",
    "pred_udp_rcvbuf_errors_per_sec",
    "forecast_ratio",
    "policy_result",
    "policy_reason",
    "decision",
    "decision_source",
    "applied",
    "result",
]


def decisions_csv_path(path: Path | None = None) -> Path:
    return path or state_dir() / "decisions.csv"


def ensure_decisions_csv(path: Path | None = None) -> Path:
    csv_path = decisions_csv_path(path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if not csv_path.exists():
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADERS)
            writer.writeheader()
    return csv_path


def append_decision(
    reviewed: ReviewedProposal,
    *,
    decision: str,
    decision_source: str,
    applied: bool,
    result: str,
    csv_path: Path | None = None,
) -> None:
    path = decisions_csv_path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = True
    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as handle:
            first_line = handle.readline().strip()
        if first_line.split(",") == HEADERS:
            write_header = False
        else:
            legacy_path = path.with_name(f"decisions.legacy.{utc_like_now().replace(':', '')}.csv")
            path.replace(legacy_path)
    row: dict[str, Any] = {
        "timestamp": utc_like_now(),
        "cycle_id": reviewed.proposal.cycle_id,
        "proposal_id": reviewed.proposal.proposal_id,
        "key": reviewed.proposal.key,
        "current": reviewed.proposal.current,
        "proposed": reviewed.proposal.proposed,
        "short_ko": reviewed.policy.short_ko,
        "risk_level": reviewed.policy.risk_level,
        "warn_ko": " | ".join(reviewed.policy.warnings_ko),
        "model_a": reviewed.forecast.model_name,
        "model_a_dataset": reviewed.forecast.dataset_name,
        "model_a_dataset_source": reviewed.forecast.dataset_source,
        "pred_rx_mbps": reviewed.forecast.pred_rx_mbps,
        "pred_tx_mbps": reviewed.forecast.pred_tx_mbps,
        "pred_rx_pps": reviewed.forecast.pred_rx_pps,
        "pred_tx_pps": reviewed.forecast.pred_tx_pps,
        "pred_tcp_syn_recv": reviewed.forecast.pred_tcp_syn_recv,
        "pred_tcp_time_wait": reviewed.forecast.pred_tcp_time_wait,
        "pred_tcp_retrans_segs_per_sec": reviewed.forecast.pred_tcp_retrans_segs_per_sec,
        "pred_listen_overflows_per_sec": reviewed.forecast.pred_listen_overflows_per_sec,
        "pred_listen_drops_per_sec": reviewed.forecast.pred_listen_drops_per_sec,
        "pred_rx_drops_per_sec": reviewed.forecast.pred_rx_drops_per_sec,
        "pred_softnet_drops_per_sec": reviewed.forecast.pred_softnet_drops_per_sec,
        "pred_udp_rcvbuf_errors_per_sec": reviewed.forecast.pred_udp_rcvbuf_errors_per_sec,
        "forecast_ratio": reviewed.forecast.forecast_ratio,
        "policy_result": reviewed.policy.result,
        "policy_reason": reviewed.policy.reason,
        "decision": decision,
        "decision_source": decision_source,
        "applied": str(applied).lower(),
        "result": result,
    }
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
