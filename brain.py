import json
import re
import subprocess
from pathlib import Path
from typing import Any

from config import CONNECTION_METRICS_LOG


class Brain:
    """Adaptive scoring engine for VPN-Agent.

    The Brain parses historical connection metrics and computes a score for each
    protocol/config hash within a specific network context. It favors success
    rate first, then latency as a secondary signal.
    """

    def __init__(self, metrics_path: Path | str = CONNECTION_METRICS_LOG):
        self.metrics_path = Path(metrics_path)

    def detect_network_id(self) -> str:
        """Detect the current network context using SSID first, then ISP metadata."""
        ssid = self._detect_ssid()
        if ssid:
            return f"ssid:{ssid}"

        isp = self._detect_isp()
        if isp:
            return f"isp:{isp}"

        return "network:unknown"

    def _detect_ssid(self) -> str | None:
        try:
            output = subprocess.check_output(["iwgetid", "-r"], text=True, stderr=subprocess.DEVNULL)
            ssid = output.strip()
            return ssid if ssid else None
        except Exception:
            return None

    def _detect_isp(self) -> str | None:
        services = [
            ["curl", "-fsS", "--max-time", "3", "https://ipinfo.io/org"],
            ["curl", "-fsS", "--max-time", "3", "https://ipapi.co/org"],
        ]
        for cmd in services:
            try:
                output = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
                isp = output.strip()
                if isp:
                    return isp
            except Exception:
                continue
        return None

    def load_metrics(self) -> list[dict[str, Any]]:
        """Load historical metrics from the log file."""
        if not self.metrics_path.exists():
            return []

        entries: list[dict[str, Any]] = []
        for line in self.metrics_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                metrics = json.loads(line)
                entries.append(metrics)
            except json.JSONDecodeError:
                continue
        return entries

    def _latency_factor(self, latency: str | None) -> float:
        """Convert a latency string to a 0.0-1.0 factor for scoring."""
        if not latency or latency == "N/A":
            return 0.25

        match = re.search(r"(\d+(?:\.\d+)?)", latency)
        if not match:
            return 0.25

        value = float(match.group(1))
        normalized = max(0.0, min(1.0, 1.0 - ((value - 20.0) / 480.0)))
        return normalized

    def _aggregate_metrics(self) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
        """Aggregate metrics by network, protocol, and config hash."""
        aggregated: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
        for row in self.load_metrics():
            network_id = row.get("network_id") or "network:unknown"
            protocol = row.get("protocol") or "unknown"
            config_hash = row.get("config_hash") or "unknown"
            network = aggregated.setdefault(network_id, {})
            protocol_bucket = network.setdefault(protocol, {})
            stats = protocol_bucket.setdefault(config_hash, {
                "success_count": 0,
                "total_count": 0,
                "latencies": [],
            })

            stats["total_count"] += 1
            if row.get("success"):
                stats["success_count"] += 1
            latency = row.get("latency")
            if isinstance(latency, str) and latency != "N/A":
                stats["latencies"].append(latency)

        return aggregated

    def _score_entry(self, entry: dict[str, Any]) -> float:
        success_rate = entry["success_count"] / entry["total_count"] if entry["total_count"] else 0.0
        avg_latency = self._average_latency(entry["latencies"])
        latency_factor = self._latency_factor(avg_latency)
        return success_rate * 0.7 + latency_factor * 0.3

    def _average_latency(self, latencies: list[str]) -> str | None:
        if not latencies:
            return None
        numeric = []
        for latency in latencies:
            match = re.search(r"(\d+(?:\.\d+)?)", latency)
            if match:
                numeric.append(float(match.group(1)))
        if not numeric:
            return None
        return f"{sum(numeric) / len(numeric)}"

    def scores_for_network(self, network_id: str) -> dict[str, dict[str, float]]:
        """Return scored configs for a network grouped by protocol."""
        aggregated = self._aggregate_metrics()
        network_metrics = aggregated.get(network_id, {})
        scored: dict[str, dict[str, float]] = {}

        for protocol, configs in network_metrics.items():
            scored[protocol] = {
                config_hash: self._score_entry(entry)
                for config_hash, entry in configs.items()
            }

        return scored

    def best_config_hash(self, protocol: str, network_id: str) -> str | None:
        """Choose the best historic config hash for a protocol in the current network."""
        scores = self.scores_for_network(network_id).get(protocol, {})
        if not scores:
            return None
        return max(scores, key=scores.get)

    def recommend_protocol_order(self, network_id: str, fallback_order: list[str]) -> list[str]:
        """Reorder protocols based on historical network-specific performance."""
        scores = self.scores_for_network(network_id)
        weighted: dict[str, float] = {}
        for protocol in fallback_order:
            protocol_scores = scores.get(protocol, {})
            weighted[protocol] = max(protocol_scores.values()) if protocol_scores else 0.0

        return sorted(fallback_order, key=lambda name: (-weighted[name], fallback_order.index(name)))
