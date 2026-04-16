import os
from pathlib import Path

# --- Project Metadata ---
VERSION = "0.1.0"

# --- Path Configuration ---
# We use SUDO_USER to ensure we stay in the human user's home directory 
# even when the script is executed with root privileges.
real_user = os.environ.get('SUDO_USER') or os.environ.get('USER')
BASE_DIR = Path(f"/home/{real_user}/.config/vpn-agent")

# Ensure the config directory exists
BASE_DIR.mkdir(parents=True, exist_ok=True)

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
}

# --- Logic Settings ---
FALLBACK_ORDER = ["wg", "awg"]
CHECK_IP = "8.8.8.8"  # Google DNS used for connectivity heartbeat

# Timing (seconds)
CONNECT_WAIT = {
    "wg": 2, 
    "awg": 3
}

# Retry & Recovery Logic
WG_ATTEMPTS = 3      # Number of retries for standard WG before falling back
RECOVERY_CHECK = 30  # Interval (sec) to check if a better protocol is available