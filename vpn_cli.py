import subprocess
import time
import os
import sys
import argparse
import re

from config import (
    VERSION, PROTOCOLS, FALLBACK_ORDER, CHECK_IP, 
    CONNECT_WAIT, WG_ATTEMPTS, RECOVERY_CHECK
)

# ─── NETWORK UTILITIES ────────────────────────────────────────────────────────

def get_latency(iface: str) -> str:
    """
    Calculates average RTT latency to CHECK_IP through a specific interface.
    Returns formatted string (e.g., '45.2 ms') or 'N/A' on failure.
    """
    try:
        # Perform 3 fast pings (-i 0.2) to get an average
        output = subprocess.check_output(
            ["ping", "-c", "3", "-i", "0.2", "-W", "2", "-I", iface, CHECK_IP],
            text=True, stderr=subprocess.DEVNULL
        )
        # Regex to extract the 'avg' value from the ping summary line
        match = re.search(r"(\d+\.\d+)/(\d+\.\d+)/(\d+\.\d+)", output)
        if match:
            return f"{match.group(2)} ms"
    except Exception:
        pass
    return "N/A"

def is_internet_up(iface: str) -> bool:
    """Check if the internet is reachable via the provided tunnel interface."""
    try:
        subprocess.check_call(
            ["ping", "-c", "1", "-W", "2", "-I", iface, CHECK_IP],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return True
    except subprocess.CalledProcessError:
        return False

def run_cmd(command: list) -> tuple[bool, str]:
    """Helper to run system commands and capture potential errors."""
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        return True, ""
    except subprocess.CalledProcessError as e:
        return False, e.stderr.strip()

def get_active_vpn():
    """Returns (protocol_name, config_dict) for the currently running tunnel."""
    result = subprocess.run(["ip", "addr", "show"], capture_output=True, text=True)
    for name, proto in PROTOCOLS.items():
        if proto["iface"] in result.stdout:
            return name, proto
    return None, None

def stop_all():
    """Cleanly shuts down any active WireGuard or AmneziaWG interfaces."""
    for proto in PROTOCOLS.values():
        run_cmd([proto["cmd"], "down", str(proto["conf"])])

# ─── CORE LOGIC ───────────────────────────────────────────────────────────────

def connect():
    """
    Implements the core connection logic: 
    1. Tries Standard WireGuard with retries.
    2. Falls back to AmneziaWG if DPI blocking is detected.
    """
    stop_all()
    
    # Try high-performance protocol first
    wg_proto = PROTOCOLS["wg"]
    for i in range(1, WG_ATTEMPTS + 1):
        print(f"🚀 [Attempt {i}/{WG_ATTEMPTS}] Trying {wg_proto['label']}...")
        run_cmd([wg_proto["cmd"], "up", str(wg_proto["conf"])])
        time.sleep(CONNECT_WAIT["wg"])
        
        if is_internet_up(wg_proto["iface"]):
            print(f"✅ {wg_proto['label']} active. Stealth not required.")
            return "wg"
        
        print(f"⚠️  {wg_proto['label']} attempt {i} failed.")
        run_cmd([wg_proto["cmd"], "down", str(wg_proto["conf"])])

    # Fallback to obfuscated protocol
    print("🛡️ Switching to AmneziaWG (Obfuscated Fallback)...")
    awg_proto = PROTOCOLS["awg"]
    run_cmd([awg_proto["cmd"], "up", str(awg_proto["conf"])])
    time.sleep(CONNECT_WAIT["awg"])
    
    if is_internet_up(awg_proto["iface"]):
        print("✅ AmneziaWG active. Connection successfully obfuscated.")
        return "awg"
    
    print("❌ Critical: All protocols failed. Check server status.")
    stop_all()
    return None

def daemon_mode():
    """
    Background monitor that ensures connection stability and 
    periodically attempts to 'upgrade' back to standard WireGuard.
    """
    print(f"🏃 Daemon started: Monitoring every {RECOVERY_CHECK}s...")
    while True:
        time.sleep(10) # Base heartbeat
        name, proto = get_active_vpn()
        
        if not name:
            print("🔴 Connection lost! Initiating reconnection...")
            connect()
            continue

        if not is_internet_up(proto["iface"]):
            print(f"⚠️  {proto['label']} tunnel collapsed. Repairing...")
            connect()
            continue

        # Recovery Logic: If currently on AWG, check if WG is now available
        if name == "awg":
            print(f"🔍 Periodic Check: Can we revert to Standard WG?")
            if connect() == "wg":
                print("♻️  Successfully recovered back to Standard WireGuard!")
            else:
                print("🛡️  Standard WG still blocked. Maintaining AmneziaWG.")

def show_status():
    """Prints a professional dashboard of the current connection status."""
    name, proto = get_active_vpn()
    if proto is None:
        print("Status: 🔴 Disconnected")
        return

    latency = get_latency(proto["iface"])
    
    print(f"Status: 🟢 Connected via {proto['label']}")
    print(f"Latency: ⚡ {latency}")
    print("-" * 40)
    
    # Show real-time transfer data from the kernel module
    result = subprocess.run([proto["show_cmd"], "show", proto["iface"]], capture_output=True, text=True)
    print(result.stdout.strip())

# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

def main():
    # Enforce root privileges for network manipulation
    if os.getuid() != 0:
        print("❌ Access Denied: Please run with sudo.")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="VPN-Agent: Intelligent WG/AWG Switching CLI",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    # Version flag
    parser.add_argument("-v", "--version", action="version", version=f"VPN-Agent {VERSION}")
    
    # Commands
    parser.add_argument(
        "command", 
        nargs="?", # Command optional to allow -v to work
        choices=["up", "down", "status", "daemon"],
        help="up: Connect | down: Stop | status: Show Info | daemon: Auto-Recover"
    )
    
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "up":
        connect()
    elif args.command == "down":
        stop_all()
        print("🛑 VPN Disconnected.")
    elif args.command == "status":
        show_status()
    elif args.command == "daemon":
        daemon_mode()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Agent stopped by user.")