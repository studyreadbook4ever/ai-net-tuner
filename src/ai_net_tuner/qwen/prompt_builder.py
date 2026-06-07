from __future__ import annotations

import json
import os

from ai_net_tuner.models import Forecast, TrafficSnapshot


def _pressure_summary(traffic: TrafficSnapshot, forecast: Forecast) -> dict:
    return {
        "throughput": {
            "forecast_ratio": forecast.forecast_ratio,
            "current_rx_mbps": traffic.rx_mbps,
            "current_tx_mbps": traffic.tx_mbps,
            "pred_rx_mbps": forecast.pred_rx_mbps,
            "pred_tx_mbps": forecast.pred_tx_mbps,
            "pred_rx_pps": forecast.pred_rx_pps,
            "pred_tx_pps": forecast.pred_tx_pps,
        },
        "connection_churn": {
            "current_active_opens_per_sec": traffic.tcp_active_opens_per_sec,
            "current_passive_opens_per_sec": traffic.tcp_passive_opens_per_sec,
            "pred_active_opens_per_sec": forecast.pred_tcp_active_opens_per_sec,
            "pred_passive_opens_per_sec": forecast.pred_tcp_passive_opens_per_sec,
            "current_time_wait": traffic.tcp_time_wait,
            "pred_time_wait": forecast.pred_tcp_time_wait,
            "current_syn_recv": traffic.tcp_syn_recv,
            "pred_syn_recv": forecast.pred_tcp_syn_recv,
        },
        "listen_backlog_pressure": {
            "current_listen_overflows_per_sec": traffic.listen_overflows_per_sec,
            "current_listen_drops_per_sec": traffic.listen_drops_per_sec,
            "pred_listen_overflows_per_sec": forecast.pred_listen_overflows_per_sec,
            "pred_listen_drops_per_sec": forecast.pred_listen_drops_per_sec,
        },
        "loss_and_retransmission": {
            "current_tcp_retrans_segs_per_sec": traffic.tcp_retrans_segs_per_sec,
            "current_tcp_syn_retrans_per_sec": traffic.tcp_syn_retrans_per_sec,
            "pred_tcp_retrans_segs_per_sec": forecast.pred_tcp_retrans_segs_per_sec,
            "pred_tcp_syn_retrans_per_sec": forecast.pred_tcp_syn_retrans_per_sec,
            "current_rx_drops_per_sec": traffic.rx_drops_per_sec,
            "pred_rx_drops_per_sec": forecast.pred_rx_drops_per_sec,
            "current_softnet_drops_per_sec": traffic.softnet_drops_per_sec,
            "pred_softnet_drops_per_sec": forecast.pred_softnet_drops_per_sec,
        },
        "udp_pressure": {
            "current_udp_in_datagrams_per_sec": traffic.udp_in_datagrams_per_sec,
            "current_udp_out_datagrams_per_sec": traffic.udp_out_datagrams_per_sec,
            "current_udp_in_errors_per_sec": traffic.udp_in_errors_per_sec,
            "current_udp_rcvbuf_errors_per_sec": traffic.udp_rcvbuf_errors_per_sec,
            "pred_udp_in_errors_per_sec": forecast.pred_udp_in_errors_per_sec,
            "pred_udp_rcvbuf_errors_per_sec": forecast.pred_udp_rcvbuf_errors_per_sec,
        },
        "socket_pressure": {
            "sockets_used": traffic.sockets_used,
            "tcp_inuse": traffic.tcp_inuse,
            "tcp_orphan": traffic.tcp_orphan,
            "tcp_tw_sockstat": traffic.tcp_tw_sockstat,
            "tcp_alloc": traffic.tcp_alloc,
            "tcp_mem_pages": traffic.tcp_mem_pages,
            "udp_inuse": traffic.udp_inuse,
        },
        "proposal_hints": [
            "In coursework mode, prefer bounded plausible proposals over staying silent.",
            "Consider listen backlog knobs when SYN_RECV, passive opens, ListenOverflows, or ListenDrops rise.",
            "Consider netdev backlog when packet rate, RX drops, softnet drops, or softnet time_squeeze rise.",
            "Consider receive/send buffer knobs when throughput, UDP/TCP buffer errors, or drops rise.",
            "Consider TIME_WAIT or ephemeral port knobs when TIME_WAIT or active connection churn rises.",
            "Return zero proposals only when no supplied sysctl key has a plausible connection to the pressure signals."
        ],
    }


def _decision_guide() -> list[dict]:
    return [
        {
            "signals": ["pred_listen_overflows_per_sec", "pred_listen_drops_per_sec", "pred_syn_recv", "pred_passive_opens_per_sec"],
            "candidate_keys": ["net.core.somaxconn", "net.ipv4.tcp_max_syn_backlog"],
            "direction": "increase backlog capacity by a moderate step when current values are modest and pressure is rising",
        },
        {
            "signals": ["pred_rx_pps", "pred_rx_drops_per_sec", "pred_softnet_drops_per_sec"],
            "candidate_keys": ["net.core.netdev_max_backlog"],
            "direction": "increase receive backlog capacity by a moderate step for burst absorption",
        },
        {
            "signals": ["pred_rx_mbps", "pred_udp_rcvbuf_errors_per_sec", "pred_rx_drops_per_sec"],
            "candidate_keys": ["net.core.rmem_max", "net.core.rmem_default", "net.ipv4.tcp_rmem", "net.ipv4.udp_rmem_min", "net.ipv4.tcp_moderate_rcvbuf"],
            "direction": "increase receive buffer headroom gradually or enable receive autotuning when receive pressure is visible",
        },
        {
            "signals": ["pred_tx_mbps", "pred_tx_pps"],
            "candidate_keys": ["net.core.wmem_max", "net.core.wmem_default", "net.ipv4.tcp_wmem", "net.ipv4.udp_wmem_min"],
            "direction": "increase send buffer headroom gradually when transmit pressure is rising",
        },
        {
            "signals": ["pred_time_wait", "pred_active_opens_per_sec"],
            "candidate_keys": ["net.ipv4.ip_local_port_range", "net.ipv4.tcp_tw_reuse", "net.ipv4.tcp_max_tw_buckets", "net.ipv4.tcp_fin_timeout"],
            "direction": "expand or recycle connection state capacity when connection churn is high",
        },
        {
            "signals": ["pred_tcp_retrans_segs_per_sec", "pred_tcp_syn_retrans_per_sec"],
            "candidate_keys": ["net.ipv4.tcp_mtu_probing", "net.ipv4.tcp_ecn", "net.ipv4.tcp_ecn_fallback", "net.ipv4.tcp_slow_start_after_idle"],
            "direction": "adjust congestion or path handling only as exploratory medium/high risk proposals",
        },
        {
            "signals": ["sockets_used", "tcp_inuse", "tcp_orphan", "tcp_alloc", "udp_inuse"],
            "candidate_keys": ["net.core.optmem_max", "net.ipv4.tcp_orphan_retries", "net.ipv4.tcp_keepalive_time", "net.ipv4.tcp_keepalive_intvl", "net.ipv4.tcp_keepalive_probes"],
            "direction": "adjust socket memory or stale connection cleanup when socket pressure is visible",
        },
    ]


def _compact_docs(docs: list[dict]) -> list[dict]:
    limit_raw = os.environ.get("AI_NET_TUNER_PROMPT_DOC_LIMIT", "60")
    try:
        limit = max(1, int(limit_raw))
    except ValueError:
        limit = 32

    compact = []
    for entry in docs[:limit]:
        compact.append(
            {
                "key": entry.get("key"),
                "keys": entry.get("keys", [])[:8],
                "risk": entry.get("risk"),
                "role": entry.get("auto_tuning_role"),
                "summary": entry.get("summary"),
                "helps": entry.get("when_it_may_help"),
                "tradeoffs": entry.get("tradeoffs", [])[:5],
                "signals": entry.get("signals", [])[:5],
            }
        )
    return compact


def build_qwen_prompt(
    traffic: TrafficSnapshot,
    forecast: Forecast,
    current_sysctls: dict[str, str],
    docs: list[dict],
    initial_sysctls: dict[str, str] | None = None,
) -> str:
    payload = {
        "task": "Generate Linux network sysctl tuning proposals from traffic forecasts.",
        "hard_rules": [
            "Respond in English only.",
            "Return valid JSON only.",
            "Do not write Korean.",
            "Do not claim certainty; all effects are estimates.",
            "Do not propose keys not supported by the supplied documentation context.",
            "Use current_sysctls as the authoritative current values.",
            "Do not propose keys whose current_sysctls value is unknown.",
            "If the proposed value equals the current value, return zero proposals for that key.",
            "This is a human-in-the-loop coursework demo: prefer bounded proposals over silence.",
            "Return 1 to 2 proposals when any supplied pressure signal can be plausibly mapped to supplied sysctl documentation.",
            "Prefer moderate step changes, not extreme jumps.",
            "Do not choose a maximum allowed value just because the policy allows it.",
            "Return zero proposals only when every plausible proposal would be identical to current_sysctls or unsupported by supplied documentation."
        ],
        "exploration_policy": {
            "target_proposals_per_cycle": "1-2",
            "precision_recall_preference": "balanced recall with bounded value changes; policy guardrails and the human operator will still reject poor proposals",
            "integer_step_policy": "for integer knobs, prefer 25% to 100% changes from the current value; avoid more than 2x current unless the current value is clearly tiny",
            "triplet_step_policy": "for three-integer buffer knobs, preserve nondecreasing order and adjust values proportionally",
            "enum_step_policy": "for enum knobs, choose only a documented adjacent or standard mode",
            "acceptable_reasoning_style": "short English reasons that connect one pressure signal to one sysctl tradeoff",
        },
        "output_schema": {
            "proposals": [
                {
                    "key": "net.core.somaxconn",
                    "current": "1024",
                    "proposed": "4096",
                    "reason_en": "Predicted inbound connection bursts may pressure the accept backlog.",
                    "expected_effect_en": "May reduce pending connection queue pressure.",
                    "risk_level": "medium"
                }
            ]
        },
        "traffic_model": {
            "name": forecast.model_name,
            "horizon_seconds": forecast.horizon_seconds,
            "dataset": forecast.dataset_name,
            "dataset_source": forecast.dataset_source,
        },
        "pressure_summary": _pressure_summary(traffic, forecast),
        "traffic_to_sysctl_decision_guide": _decision_guide(),
        "initial_sysctls_at_run_start": initial_sysctls or current_sysctls,
        "current_sysctls": current_sysctls,
        "official_docs_context": _compact_docs(docs),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
