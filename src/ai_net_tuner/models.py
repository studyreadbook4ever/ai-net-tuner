from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class TrafficSnapshot:
    timestamp: str
    rx_mbps: float
    tx_mbps: float
    rx_pps: float
    tx_pps: float
    tcp_established: int
    tcp_time_wait: int
    tcp_syn_recv: int
    tcp_fin_wait2: int
    rx_drops: int
    tx_drops: int
    rx_drops_per_sec: float
    tx_drops_per_sec: float
    rx_errors_per_sec: float
    tx_errors_per_sec: float
    tcp_active_opens_per_sec: float
    tcp_passive_opens_per_sec: float
    tcp_attempt_fails_per_sec: float
    tcp_estab_resets_per_sec: float
    tcp_retrans_segs_per_sec: float
    tcp_in_segs_per_sec: float
    tcp_out_segs_per_sec: float
    tcp_out_rsts_per_sec: float
    tcp_syn_retrans_per_sec: float
    listen_overflows_per_sec: float
    listen_drops_per_sec: float
    udp_in_datagrams_per_sec: float
    udp_out_datagrams_per_sec: float
    udp_in_errors_per_sec: float
    udp_no_ports_per_sec: float
    udp_rcvbuf_errors_per_sec: float
    udp_sndbuf_errors_per_sec: float
    softnet_drops_per_sec: float
    softnet_time_squeeze_per_sec: float
    sockets_used: int
    tcp_inuse: int
    tcp_orphan: int
    tcp_tw_sockstat: int
    tcp_alloc: int
    tcp_mem_pages: int
    udp_inuse: int
    source: str = "local_proc"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Forecast:
    model_name: str
    horizon_seconds: int
    dataset_name: str
    dataset_source: str
    pred_rx_mbps: float
    pred_tx_mbps: float
    pred_rx_pps: float
    pred_tx_pps: float
    pred_tcp_established: int
    pred_tcp_time_wait: int
    pred_tcp_syn_recv: int
    pred_tcp_fin_wait2: int
    pred_rx_drops: int
    pred_rx_drops_per_sec: float
    pred_tx_drops_per_sec: float
    pred_rx_errors_per_sec: float
    pred_tx_errors_per_sec: float
    pred_tcp_active_opens_per_sec: float
    pred_tcp_passive_opens_per_sec: float
    pred_tcp_retrans_segs_per_sec: float
    pred_tcp_syn_retrans_per_sec: float
    pred_listen_overflows_per_sec: float
    pred_listen_drops_per_sec: float
    pred_udp_in_errors_per_sec: float
    pred_udp_rcvbuf_errors_per_sec: float
    pred_softnet_drops_per_sec: float
    forecast_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Proposal:
    proposal_id: str
    cycle_id: str
    created_at: str
    key: str
    current: str
    proposed: str
    reason_en: str
    expected_effect_en: str
    risk_level: str
    source_model: str = "qwen-offline-stub"
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PolicyDecision:
    allowed: bool
    result: str
    risk_level: str
    short_ko: str
    evidence_ko: str
    warnings_ko: list[str]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewedProposal:
    proposal: Proposal
    policy: PolicyDecision
    traffic: TrafficSnapshot
    forecast: Forecast
    sysctl_docs: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal": self.proposal.to_dict(),
            "policy": self.policy.to_dict(),
            "traffic": self.traffic.to_dict(),
            "forecast": self.forecast.to_dict(),
            "sysctl_docs": self.sysctl_docs,
        }


def utc_like_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
