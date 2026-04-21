import json
import re
import subprocess
from pathlib import Path
from typing import Any

from config import CONNECTION_METRICS_LOG, BASE_DIR
from database import BrainDatabase


class Brain:
    """Adaptive scoring engine for VPN-Agent.

    The Brain parses historical connection metrics and computes a score for each
    protocol/config hash within a specific network context. It favors success
    rate first, then latency as a secondary signal.
    """

    def __init__(self, db_path: Path | str = BASE_DIR / "agent_brain.db"):
        # Brain is intentionally a thin wrapper over BrainDatabase:
        # network detection lives here, while persistence/scoring lives in database.py.
        self.db = BrainDatabase(Path(db_path))
        # Migrate old JSON metrics if they exist
        self._migrate_json_metrics()

    def _migrate_json_metrics(self):
        """Migrate existing JSON metrics to database on first run."""
        json_path = Path(CONNECTION_METRICS_LOG)
        if json_path.exists():
            self.db.migrate_json_metrics(json_path)
            # Rename the old file to prevent re-migration
            json_path.rename(json_path.with_suffix('.log.bak'))

    def detect_network_id(self) -> str:
        """Detect the current network context using SSID first, then ISP metadata."""
        # SSID is the most precise signal because the same ISP can appear in many
        # places with very different filtering behavior.
        ssid = self._detect_ssid()
        if ssid:
            return f"ssid:{ssid}"

        # If Wi-Fi metadata is unavailable, fall back to an ISP fingerprint.
        # This is weaker than SSID, but still better than treating every network
        # as completely unknown.
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
        # Use multiple services because captive portals or regional filtering
        # can block one endpoint but not another.
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
        """Load historical metrics from the database."""
        # This method is kept for backward compatibility but now returns data from DB
        # In the new implementation, we use direct DB queries in other methods
        return []

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
        """Aggregate metrics by network, protocol, and config hash from database."""
        # This method is now deprecated as we use direct SQL queries
        return {}

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
        """Return scored configs for a network grouped by protocol from database."""
        scored: dict[str, dict[str, float]] = {}
        # `get_ranked_configs()` already applies stale filtering and recency decay,
        # so the Brain only needs to reshape the rows by protocol/hash.
        for config in self.db.get_ranked_configs(network_id):
            if config.protocol not in scored:
                scored[config.protocol] = {}
            scored[config.protocol][config.config_hash] = config.reliability_score

        return scored

    def best_config_hash(self, protocol: str, network_id: str) -> str | None:
        """Choose the best historic config hash for a protocol in the current network."""
        ranked_hashes = self.ranked_config_hashes(protocol, network_id)
        return ranked_hashes[0] if ranked_hashes else None

    def ranked_config_hashes(self, protocol: str, network_id: str) -> list[str]:
        """Return scored config hashes for a protocol in rank order."""
        return [entry.config_hash for entry in self.db.get_ranked_configs(network_id, protocol=protocol)]

    def recommend_protocol_order(self, network_id: str, fallback_order: list[str]) -> list[str]:
        """Reorder protocols based on historical network-specific performance."""
        ranked_configs = self.db.get_ranked_configs(network_id)
        best_by_protocol: dict[str, float] = {}
        for entry in ranked_configs:
            # `ranked_configs` arrives sorted best-first, so the first time we see
            # a protocol is the score we want to represent that protocol.
            if entry.protocol in fallback_order and entry.protocol not in best_by_protocol:
                best_by_protocol[entry.protocol] = entry.reliability_score
        weighted: dict[str, float] = {}
        for protocol in fallback_order:
            # Unknown protocols keep a score of 0.0, which preserves the user's
            # original fallback order instead of inventing a preference.
            weighted[protocol] = best_by_protocol.get(protocol, 0.0)

        return sorted(fallback_order, key=lambda name: (-weighted[name], fallback_order.index(name)))
