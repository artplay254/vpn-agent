import hashlib
import json
import os
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import pwd

# --- Project Metadata ---
VERSION = "0.4.5"

# --- Path Configuration ---
real_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "root"
try:
    home_dir = Path(pwd.getpwnam(real_user).pw_dir)
except KeyError:
    home_dir = Path.home()

BASE_DIR = home_dir / ".config" / "vpn-agent"
BASE_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = BASE_DIR / "agent.log"
XRAY_LOG_FILE = BASE_DIR / "xray.log"
CONNECTION_METRICS_LOG = BASE_DIR / "connection_metrics.log"
PID_DIR = BASE_DIR / "run"
os.makedirs(PID_DIR, exist_ok=True)
XRAY_PID_FILE = PID_DIR / "xray.pid"
DAEMON_PID_FILE = PID_DIR / "daemon.pid"

# --- Protocol Definitions ---
PROTOCOLS = {
    "wg": {
        "conf":       BASE_DIR / "client_wg.conf",
        "iface":      "client_wg",
        "cmd":        "wg-quick",
        "show_cmd":   "wg",
        "obfuscated": False,
        "label":      "Standard WireGuard",
    },
    "awg": {
        "conf":       BASE_DIR / "client_awg.conf",
        "iface":      "client_awg",
        "cmd":        "awg-quick",
        "show_cmd":   "awg",
        "obfuscated": True,
        "label":      "AmneziaWG",
    },
    "vless": {
        "conf":       BASE_DIR / "vless.json",
        # XRay may ignore `interfaceName` and create `xray0`, `xray1`, ...
        # `vpn_cli.py status`/connect auto-detects the active `xray*` interface.
        "iface":      "xray0",
        # The protocol is VLESS, implemented by the `xray` binary.
        "cmd":        "xray",
        "show_cmd":   "xray",  # Placeholder
        "label":      "VLESS Reality",
        "obfuscated": True,
    }
}

# --- Logic Settings ---
FALLBACK_ORDER = ["wg", "awg", "vless"]
CHECK_IP = "1.1.1.1" 
CONNECT_WAIT = {"wg": 2, "awg": 3, "vless": 4}

# Retry & Recovery Logic
WG_ATTEMPTS = 3      
RECOVERY_CHECK = 300  # Increased to 5 mins to avoid frequent connection drops

# MTU Discovery range
MTU_START = 1492
MTU_MIN = 1280
MTU_MAX = 1500
MTU_STEP = 8

# Adaptive mutation ranges
AWG_JC_RANGE = (1, 14)
AWG_JMIN_RANGE = (20, 70)
AWG_JMAX_RANGE = (40, 120)
VLESS_PORT_OPTIONS = [443, 8443, 10443, 4433]
VLESS_MTU_RANGE = (1280, 1500)

# Variant storage
CONFIG_VARIANT_DIR = BASE_DIR / "variants"
CONFIG_VARIANT_DIR.mkdir(parents=True, exist_ok=True)
VARIANT_INDEX_FILE = CONFIG_VARIANT_DIR / "variant_index.json"

REPO_DIR = Path(__file__).parent
TEMPLATE_FILES = {
    "wg": REPO_DIR / "client_wg.conf.example",
    "awg": REPO_DIR / "client_awg.conf.example",
    "vless": REPO_DIR / "vless.json.example",
}
TEMPLATE_EXTENSIONS = {
    "wg": ".conf",
    "awg": ".conf",
    "vless": ".json",
}

@dataclass
class ConfigVariant:
    """A generated configuration variant.

    Each variant is uniquely tracked by the SHA256 hash of its content.
    """
    protocol: str
    path: Path
    alias: str
    config_hash: str
    params: dict


class ConfigMutator:
    """Generate configuration variants from templates with safe mutation ranges."""

    def _load_index(self) -> dict:
        if not VARIANT_INDEX_FILE.exists():
            return {}
        try:
            return json.loads(VARIANT_INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_index(self, index: dict) -> None:
        temp_file = VARIANT_INDEX_FILE.with_suffix('.tmp')
        temp_file.write_text(json.dumps(index, indent=2), encoding="utf-8")
        temp_file.replace(VARIANT_INDEX_FILE)

    def _ensure_protocol_index(self, index: dict) -> dict:
        if self.protocol not in index:
            index[self.protocol] = {"hash_to_alias": {}, "alias_to_hash": {}}
        return index[self.protocol]

    def _alias_for_hash(self, config_hash: str) -> str | None:
        index = self._load_index()
        protocol_index = index.get(self.protocol, {})
        return protocol_index.get("hash_to_alias", {}).get(config_hash)

    def _next_alias(self, index: dict) -> str:
        protocol_index = self._ensure_protocol_index(index)
        aliases = protocol_index.get("alias_to_hash", {}).keys()
        max_version = 0
        for alias in aliases:
            parts = alias.split("_v")
            if len(parts) == 2 and parts[0] == self.protocol:
                try:
                    num = int(parts[1])
                    max_version = max(max_version, num)
                except ValueError:
                    continue
        return f"{self.protocol}_v{max_version + 1}"

    def _register_variant(self, alias: str, config_hash: str) -> None:
        index = self._load_index()
        protocol_index = self._ensure_protocol_index(index)
        protocol_index["hash_to_alias"][config_hash] = alias
        protocol_index["alias_to_hash"][alias] = config_hash
        self._save_index(index)

    def __init__(self, protocol: str):
        if protocol not in PROTOCOLS:
            raise ValueError(f"Unknown protocol for mutation: {protocol}")
        self.protocol = protocol
        self.template_path = TEMPLATE_FILES.get(protocol)
        self.extension = TEMPLATE_EXTENSIONS.get(protocol, ".conf")

    def _read_template(self) -> str:
        if self.template_path and self.template_path.exists():
            return self.template_path.read_text(encoding="utf-8")

        default_path = PROTOCOLS[self.protocol]["conf"]
        if default_path.exists():
            return default_path.read_text(encoding="utf-8")

        raise FileNotFoundError(f"No template or configuration file found for {self.protocol}")

    @staticmethod
    def compute_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def find_variant_path(self, config_hash: str) -> Path | None:
        alias = self._alias_for_hash(config_hash)
        if alias:
            candidate = CONFIG_VARIANT_DIR / f"{alias}{self.extension}"
            if candidate.exists():
                return candidate

        static_path = PROTOCOLS[self.protocol]["conf"]
        if static_path.exists():
            try:
                if self.compute_hash(static_path.read_text(encoding="utf-8")) == config_hash:
                    return static_path
            except Exception:
                pass
        return None

    def _random_params(self) -> dict:
        if self.protocol == "awg":
            jmin = random.randint(*AWG_JMIN_RANGE)
            jmax = random.randint(max(jmin + 10, AWG_JMAX_RANGE[0]), AWG_JMAX_RANGE[1])
            return {
                "MTU": random.randrange(MTU_MIN, MTU_START + 1, MTU_STEP),
                "Jc": random.randint(*AWG_JC_RANGE),
                "Jmin": jmin,
                "Jmax": jmax,
            }
        if self.protocol == "wg":
            return {
                "MTU": random.randrange(MTU_MIN, MTU_START + 1, MTU_STEP),
            }
        if self.protocol == "vless":
            return {
                "port": random.choice(VLESS_PORT_OPTIONS),
                "mtu": random.randrange(VLESS_MTU_RANGE[0], VLESS_MTU_RANGE[1] + 1, MTU_STEP),
            }
        return {}

    def _guided_mtu(self, current_mtu: int, current_score: float, previous_mtu: int | None, previous_score: float | None) -> int:
        if previous_mtu is not None and previous_score is not None:
            if current_score > previous_score:
                if current_mtu < previous_mtu:
                    return max(MTU_MIN, current_mtu - 20)
                return min(MTU_START, current_mtu + 20)
            return min(MTU_START, max(MTU_MIN, current_mtu + 20))

        return random.randrange(MTU_MIN, MTU_START + 1, 20)

    def _choose_vless_port(self, network_id: str, db) -> int:
        risky_ports = set(db.get_risky_ports(network_id))
        safe_options = [p for p in VLESS_PORT_OPTIONS if p not in risky_ports]
        if safe_options:
            return random.choice(safe_options)

        common_ports = [443, 8443, 10443, 4433, 80, 8080, 1194, 51820]
        safe_common = [p for p in common_ports if p not in risky_ports]
        if safe_common:
            return random.choice(safe_common)

        for base in [443, 80, 1194, 51820]:
            for delta in (0, 100, 200, 300):
                candidate = base + delta
                if 1024 <= candidate <= 65535 and candidate not in risky_ports:
                    return candidate

        return random.choice([p for p in range(1024, 65536, 100) if p not in risky_ports] or [random.choice(VLESS_PORT_OPTIONS)])

    def _guided_params(self, network_id: str, parent_hash: str, db) -> Optional[dict]:
        try:
            history = db.get_parent_and_previous_scores(network_id, parent_hash)
        except Exception:
            history = None

        if not history:
            return None

        parent_entry, parent_score, previous_entry, previous_score = history
        current_mtu = parent_entry.mtu
        previous_mtu = previous_entry.mtu if previous_entry else None

        if self.protocol == "awg":
            mtu = self._guided_mtu(current_mtu, parent_score, previous_mtu, previous_score)
            jmin = random.randint(*AWG_JMIN_RANGE)
            jmax = random.randint(max(jmin + 10, AWG_JMAX_RANGE[0]), AWG_JMAX_RANGE[1])
            return {
                "MTU": mtu,
                "Jc": random.randint(*AWG_JC_RANGE),
                "Jmin": jmin,
                "Jmax": jmax,
            }

        if self.protocol == "wg":
            mtu = self._guided_mtu(current_mtu, parent_score, previous_mtu, previous_score)
            return {"MTU": mtu}

        if self.protocol == "vless":
            mtu = self._guided_mtu(current_mtu, parent_score, previous_mtu, previous_score)
            return {
                "port": self._choose_vless_port(network_id, db),
                "mtu": mtu,
            }

        return None

    def generate_random_variant(self, params: dict | None = None, network_id: str | None = None,
                                parent_hash: str | None = None, db: Optional[object] = None) -> ConfigVariant:
        params = params or {}
        if not params:
            if db is not None and network_id and parent_hash and random.random() >= 0.2:
                guided = self._guided_params(network_id, parent_hash, db)
                params = guided or self._random_params()
            else:
                params = self._random_params()

        content = self._build_variant_content(params)
        config_hash = self.compute_hash(content)
        existing_path = self.find_variant_path(config_hash)
        if existing_path is not None:
            alias = self._alias_for_hash(config_hash) or existing_path.stem
            return ConfigVariant(self.protocol, existing_path, alias, config_hash, params)

        index = self._load_index()
        alias = self._next_alias(index)
        variant_path = CONFIG_VARIANT_DIR / f"{alias}{self.extension}"
        variant_path.write_text(content, encoding="utf-8")
        self._register_variant(alias, config_hash)
        return ConfigVariant(self.protocol, variant_path, alias, config_hash, params)

    def _build_variant_content(self, params: dict) -> str:
        template_text = self._read_template()
        if self.protocol in ("wg", "awg"):
            return self._render_wireguard_template(template_text, params)
        if self.protocol == "vless":
            return self._render_vless_template(template_text, params)
        raise ValueError(f"Unsupported protocol for variant generation: {self.protocol}")

    def _replace_line(self, text: str, key: str, value: str) -> str:
        pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
        replacement = f"{key} = {value}"
        if pattern.search(text):
            return pattern.sub(replacement, text)
        return text

    def _render_wireguard_template(self, template_text: str, params: dict) -> str:
        rendered = template_text
        if "MTU" in params:
            rendered = self._replace_line(rendered, "MTU", params["MTU"])
            if "MTU" not in rendered:
                rendered = rendered.replace("[Peer]", f"MTU = {params['MTU']}\n\n[Peer]", 1)

        if self.protocol == "awg":
            for key in ("Jc", "Jmin", "Jmax"):
                if key in params:
                    rendered = self._replace_line(rendered, key, params[key])

        return rendered

    def _render_vless_template(self, template_text: str, params: dict) -> str:
        config = json.loads(template_text)
        for outbound in config.get("outbounds", []):
            if outbound.get("protocol") != "vless":
                continue
            for vnext in outbound.get("settings", {}).get("vnext", []):
                if "port" in params:
                    vnext["port"] = params["port"]

        for inbound in config.get("inbounds", []):
            settings = inbound.get("settings", {})
            if "mtu" in params and isinstance(settings, dict):
                if "mtu" in settings:
                    settings["mtu"] = params["mtu"]
                else:
                    settings["mtu"] = params["mtu"]

        return json.dumps(config, indent=2)
