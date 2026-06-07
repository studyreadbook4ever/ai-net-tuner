from __future__ import annotations

import json
import socket
from datetime import datetime
from pathlib import Path

from ai_net_tuner.models import TrafficSnapshot
from ai_net_tuner.paths import state_dir


TCP_STATES = {
    "01": "established",
    "02": "syn_sent",
    "03": "syn_recv",
    "04": "fin_wait1",
    "05": "fin_wait2",
    "06": "time_wait",
    "07": "close",
    "08": "close_wait",
    "09": "last_ack",
    "0A": "listen",
    "0B": "closing",
}


def _read_net_dev() -> dict[str, int]:
    path = Path("/proc/net/dev")
    if not path.exists():
        return {
            "rx_bytes": 0,
            "tx_bytes": 0,
            "rx_packets": 0,
            "tx_packets": 0,
            "rx_errors": 0,
            "tx_errors": 0,
            "rx_drops": 0,
            "tx_drops": 0,
        }

    counters = {
        "rx_bytes": 0,
        "tx_bytes": 0,
        "rx_packets": 0,
        "tx_packets": 0,
        "rx_errors": 0,
        "tx_errors": 0,
        "rx_drops": 0,
        "tx_drops": 0,
    }
    for line in path.read_text(encoding="utf-8").splitlines()[2:]:
        if ":" not in line:
            continue
        iface, raw = line.split(":", 1)
        iface = iface.strip()
        if iface == "lo":
            continue
        fields = raw.split()
        if len(fields) < 16:
            continue
        counters["rx_bytes"] += int(fields[0])
        counters["rx_packets"] += int(fields[1])
        counters["rx_errors"] += int(fields[2])
        counters["rx_drops"] += int(fields[3])
        counters["tx_bytes"] += int(fields[8])
        counters["tx_packets"] += int(fields[9])
        counters["tx_errors"] += int(fields[10])
        counters["tx_drops"] += int(fields[11])
    return counters


def _count_tcp_states(path: Path) -> dict[str, int]:
    counts = {name: 0 for name in TCP_STATES.values()}
    if not path.exists():
        return counts

    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        fields = line.split()
        if len(fields) < 4:
            continue
        state = TCP_STATES.get(fields[3].upper())
        if state:
            counts[state] += 1
    return counts


def _tcp_counts() -> dict[str, int]:
    counts = {name: 0 for name in TCP_STATES.values()}
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        partial = _count_tcp_states(table)
        for key, value in partial.items():
            counts[key] += value
    return counts


def _read_protocol_counters(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}

    result: dict[str, int] = {}
    pending: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        section, raw = line.split(":", 1)
        parts = raw.split()
        if not parts:
            continue
        if section not in pending:
            pending[section] = parts
            continue
        keys = pending.pop(section)
        for key, value in zip(keys, parts):
            try:
                result[f"{section}.{key}"] = int(value)
            except ValueError:
                continue
    return result


def _read_softnet_counters() -> dict[str, int]:
    path = Path("/proc/net/softnet_stat")
    counters = {
        "softnet_processed": 0,
        "softnet_dropped": 0,
        "softnet_time_squeeze": 0,
    }
    if not path.exists():
        return counters

    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        counters["softnet_processed"] += int(fields[0], 16)
        counters["softnet_dropped"] += int(fields[1], 16)
        counters["softnet_time_squeeze"] += int(fields[2], 16)
    return counters


def _read_sockstat(path: Path = Path("/proc/net/sockstat")) -> dict[str, int]:
    result = {
        "sockets_used": 0,
        "tcp_inuse": 0,
        "tcp_orphan": 0,
        "tcp_tw_sockstat": 0,
        "tcp_alloc": 0,
        "tcp_mem_pages": 0,
        "udp_inuse": 0,
    }
    if not path.exists():
        return result

    for line in path.read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        section, raw = line.split(":", 1)
        values = raw.split()
        pairs = dict(zip(values[0::2], values[1::2]))
        if section == "sockets":
            result["sockets_used"] = _safe_int(pairs.get("used"))
        elif section == "TCP":
            result["tcp_inuse"] = _safe_int(pairs.get("inuse"))
            result["tcp_orphan"] = _safe_int(pairs.get("orphan"))
            result["tcp_tw_sockstat"] = _safe_int(pairs.get("tw"))
            result["tcp_alloc"] = _safe_int(pairs.get("alloc"))
            result["tcp_mem_pages"] = _safe_int(pairs.get("mem"))
        elif section == "UDP":
            result["udp_inuse"] = _safe_int(pairs.get("inuse"))
    return result


def _safe_int(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def _state_path() -> Path:
    return state_dir() / "traffic_counter_snapshot.json"


def _load_previous_counters() -> dict | None:
    path = _state_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _save_current_counters(timestamp: str, counters: dict[str, int]) -> None:
    _state_path().write_text(
        json.dumps({"timestamp": timestamp, "counters": counters}, indent=2),
        encoding="utf-8",
    )


def _rate(counters: dict[str, int], previous: dict | None, key: str, elapsed_seconds: float) -> float:
    if not previous or elapsed_seconds <= 0:
        return 0.0
    old_value = int(previous.get("counters", {}).get(key, counters.get(key, 0)))
    delta = max(0, int(counters.get(key, 0)) - old_value)
    return round(delta / elapsed_seconds, 3)


def collect_snapshot(previous: TrafficSnapshot | None = None, interval_seconds: int = 180) -> TrafficSnapshot:
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    previous_counters = _load_previous_counters()
    dev = _read_net_dev()
    proto = {
        **_read_protocol_counters(Path("/proc/net/snmp")),
        **_read_protocol_counters(Path("/proc/net/netstat")),
    }
    softnet = _read_softnet_counters()
    sockstat = _read_sockstat()
    counters = {**dev, **proto, **softnet}
    tcp = _tcp_counts()

    elapsed_seconds = float(interval_seconds)
    if previous_counters:
        try:
            previous_time = datetime.fromisoformat(previous_counters["timestamp"])
            current_time = datetime.fromisoformat(timestamp)
            elapsed_seconds = max(1.0, (current_time - previous_time).total_seconds())
        except (KeyError, ValueError):
            elapsed_seconds = float(interval_seconds)

    rx_bytes_per_sec = _rate(counters, previous_counters, "rx_bytes", elapsed_seconds)
    tx_bytes_per_sec = _rate(counters, previous_counters, "tx_bytes", elapsed_seconds)
    _save_current_counters(timestamp, counters)

    return TrafficSnapshot(
        timestamp=timestamp,
        rx_mbps=round((rx_bytes_per_sec * 8) / 1_000_000, 3),
        tx_mbps=round((tx_bytes_per_sec * 8) / 1_000_000, 3),
        rx_pps=_rate(counters, previous_counters, "rx_packets", elapsed_seconds),
        tx_pps=_rate(counters, previous_counters, "tx_packets", elapsed_seconds),
        tcp_established=tcp["established"],
        tcp_time_wait=tcp["time_wait"],
        tcp_syn_recv=tcp["syn_recv"],
        tcp_fin_wait2=tcp["fin_wait2"],
        rx_drops=dev["rx_drops"],
        tx_drops=dev["tx_drops"],
        rx_drops_per_sec=_rate(counters, previous_counters, "rx_drops", elapsed_seconds),
        tx_drops_per_sec=_rate(counters, previous_counters, "tx_drops", elapsed_seconds),
        rx_errors_per_sec=_rate(counters, previous_counters, "rx_errors", elapsed_seconds),
        tx_errors_per_sec=_rate(counters, previous_counters, "tx_errors", elapsed_seconds),
        tcp_active_opens_per_sec=_rate(counters, previous_counters, "Tcp.ActiveOpens", elapsed_seconds),
        tcp_passive_opens_per_sec=_rate(counters, previous_counters, "Tcp.PassiveOpens", elapsed_seconds),
        tcp_attempt_fails_per_sec=_rate(counters, previous_counters, "Tcp.AttemptFails", elapsed_seconds),
        tcp_estab_resets_per_sec=_rate(counters, previous_counters, "Tcp.EstabResets", elapsed_seconds),
        tcp_retrans_segs_per_sec=_rate(counters, previous_counters, "Tcp.RetransSegs", elapsed_seconds),
        tcp_in_segs_per_sec=_rate(counters, previous_counters, "Tcp.InSegs", elapsed_seconds),
        tcp_out_segs_per_sec=_rate(counters, previous_counters, "Tcp.OutSegs", elapsed_seconds),
        tcp_out_rsts_per_sec=_rate(counters, previous_counters, "Tcp.OutRsts", elapsed_seconds),
        tcp_syn_retrans_per_sec=_rate(counters, previous_counters, "TcpExt.TCPSynRetrans", elapsed_seconds),
        listen_overflows_per_sec=_rate(counters, previous_counters, "TcpExt.ListenOverflows", elapsed_seconds),
        listen_drops_per_sec=_rate(counters, previous_counters, "TcpExt.ListenDrops", elapsed_seconds),
        udp_in_datagrams_per_sec=_rate(counters, previous_counters, "Udp.InDatagrams", elapsed_seconds),
        udp_out_datagrams_per_sec=_rate(counters, previous_counters, "Udp.OutDatagrams", elapsed_seconds),
        udp_in_errors_per_sec=_rate(counters, previous_counters, "Udp.InErrors", elapsed_seconds),
        udp_no_ports_per_sec=_rate(counters, previous_counters, "Udp.NoPorts", elapsed_seconds),
        udp_rcvbuf_errors_per_sec=_rate(counters, previous_counters, "Udp.RcvbufErrors", elapsed_seconds),
        udp_sndbuf_errors_per_sec=_rate(counters, previous_counters, "Udp.SndbufErrors", elapsed_seconds),
        softnet_drops_per_sec=_rate(counters, previous_counters, "softnet_dropped", elapsed_seconds),
        softnet_time_squeeze_per_sec=_rate(counters, previous_counters, "softnet_time_squeeze", elapsed_seconds),
        sockets_used=sockstat["sockets_used"],
        tcp_inuse=sockstat["tcp_inuse"],
        tcp_orphan=sockstat["tcp_orphan"],
        tcp_tw_sockstat=sockstat["tcp_tw_sockstat"],
        tcp_alloc=sockstat["tcp_alloc"],
        tcp_mem_pages=sockstat["tcp_mem_pages"],
        udp_inuse=sockstat["udp_inuse"],
        source=f"local_proc:{socket.gethostname()}",
    )
