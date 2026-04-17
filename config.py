import os
from pathlib import Path
import pwd

# --- Project Metadata ---
VERSION = "0.2.1"

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