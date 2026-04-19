#!/usr/bin/env python3
"""
VPN-Agent CLI Tool

This is the main command-line interface for VPN-Agent, a tool that manages
multi-layer VPN connections with automatic fallback to bypass DPI censorship.

It supports three protocols in fallback order:
1. WireGuard (standard)
2. AmneziaWG (obfuscated WireGuard)
3. VLESS Reality (via XRay, masks as HTTPS)

Features:
- Automatic protocol fallback on connection failure
- Daemon mode for auto-recovery
- MTU optimization for mobile carriers
- TCP-based connectivity checks (bypasses ICMP blocks)
- PID-based locking to prevent multiple daemons
- Comprehensive logging and status reporting

Usage: sudo python3 vpn_cli.py <command> [--protocol <proto>]
"""

import subprocess
import time
import os
import sys
import argparse
import re
import logging
import shutil
import signal

import json
import socket
import hashlib

from config import (
    VERSION, PROTOCOLS, FALLBACK_ORDER, CHECK_IP,
    CONNECT_WAIT, WG_ATTEMPTS, RECOVERY_CHECK, LOG_FILE,
    MTU_START, MTU_MIN,
    XRAY_LOG_FILE, XRAY_PID_FILE, DAEMON_PID_FILE,
    CONNECTION_METRICS_LOG, ConfigMutator,
)
from brain import Brain

# ─── SETUP LOGGING ────────────────────────────────────────────────────────────
# Configure logging to write to agent.log file with timestamps
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def log_event(message, level="info"):
    """Sync output to terminal and agent.log
    
    This function prints the message to stdout and also logs it to the file.
    Level can be "info" or "error" to control logging level.
    """
    print(message)
    if level == "error":
        logging.error(message)
    else:
        logging.info(message)


def compute_config_hash(config_path: str | os.PathLike) -> str:
    """Compute SHA256 hash of config file content for unique identification"""
    try:
        with open(config_path, "rb") as f:
            content = f.read()
        return hashlib.sha256(content).hexdigest()
    except Exception:
        return "unknown"


def log_connection_metrics(protocol: str, config_hash: str, mtu: int, latency: str, 
                          success: bool, duration: float, port: int | None = None, 
                          error_msg: str = "", network_id: str = "network:unknown"):
    """Log structured connection metrics in JSON format for future analysis

    This data can be used to build success rates per configuration,
    optimize MTU/port/protocol selection, and implement intelligent fallback.

    Fields:
    - timestamp: Unix timestamp
    - protocol: wg/awg/vless
    - config_hash: SHA256 of config file for uniqueness
    - mtu: MTU value used (optimized for success, min for failure)
    - port: Port number (for VLESS)
    - latency: Measured latency or "N/A"
    - success: Boolean
    - duration: Connection attempt duration in seconds
    - error_msg: Error message if failed
    - network_id: SSID or ISP context for adaptive scoring
    """
    import time
    metrics = {
        "timestamp": time.time(),
        "protocol": protocol,
        "config_hash": config_hash,
        "mtu": mtu,
        "port": port,
        "latency": latency,
        "success": success,
        "duration": duration,
        "error_msg": error_msg,
        "network_id": network_id,
    }
    try:
        with open(CONNECTION_METRICS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(metrics) + "\n")
    except Exception as e:
        log_event(f"Failed to log metrics: {e}", "error")

def find_binary(name: str) -> str | None:
    """Find the full path to a binary using shutil.which
    
    Returns the path if found, None if not on PATH.
    """
    return shutil.which(name)


def ensure_binary(name: str) -> bool:
    """Check if a required binary is available, log error if missing
    
    Returns True if found, False if missing (and logs error).
    """
    if find_binary(name) is None:
        log_event(f"Missing dependency: {name}. Please install it and ensure it is on PATH.", "error")
        return False
    return True


def write_pid_file(path: str | os.PathLike, pid: int) -> None:
    """Write a process ID to a PID file for tracking running processes
    
    Used for daemon locking and XRay process management.
    """
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(pid))
    except Exception as e:
        log_event(f"Could not write pidfile {path}: {e}", "error")


def read_pid_file(path: str | os.PathLike) -> int | None:
    """Read a process ID from a PID file
    
    Returns the PID as int, or None if file doesn't exist or invalid.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None


def remove_pid_file(path: str | os.PathLike) -> None:
    """Remove a PID file, ignoring if it doesn't exist"""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except Exception as e:
        log_event(f"Could not remove pidfile {path}: {e}", "error")


def is_process_running(pid: int) -> bool:
    """Check if a process with given PID is still running
    
    Uses os.kill with signal 0 to test without actually sending a signal.
    """
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def validate_wg_conf(path: str | os.PathLike) -> bool:
    """Validate a WireGuard config file has required [Interface] and [Peer] sections
    
    Checks if the file exists and contains the basic WG config structure.
    """
    if not os.path.exists(path):
        log_event(f"Missing configuration file: {path}", "error")
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
            if "[Interface]" not in text or "[Peer]" not in text:
                log_event(f"Invalid WireGuard config format in {path}", "error")
                return False
    except Exception as e:
        log_event(f"Could not read {path}: {e}", "error")
        return False
    return True


def validate_vless_conf(path: str | os.PathLike) -> bool:
    """Validate a VLESS XRay config file has proper JSON structure and VLESS outbound
    
    Checks if file exists, is valid JSON, and contains a VLESS outbound configuration.
    """
    if not os.path.exists(path):
        log_event(f"Missing configuration file: {path}", "error")
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            conf = json.load(f)
    except Exception as e:
        log_event(f"Invalid JSON in {path}: {e}", "error")
        return False

    outbounds = conf.get("outbounds")
    if not isinstance(outbounds, list) or not outbounds:
        log_event(f"vless.json must include at least one outbound", "error")
        return False

    for ob in outbounds:
        if ob.get("protocol") == "vless":
            settings = ob.get("settings") or {}
            if isinstance(settings, dict) and settings.get("vnext"):
                return True
    log_event(f"vless.json is missing a valid VLESS outbound configuration", "error")
    return False


def validate_protocol_config(name: str) -> bool:
    """Validate a specific protocol's configuration and dependencies
    
    Checks if the protocol is defined, config file is valid, and binary is available.
    """
    proto = PROTOCOLS.get(name)
    if not proto:
        log_event(f"Unknown protocol: {name}", "error")
        return False
    if name in ("wg", "awg"):
        return validate_wg_conf(proto["conf"])
    if name == "vless":
        return validate_vless_conf(proto["conf"])
    return False


def filter_available_protocols(order: list[str]) -> list[str]:
    """Filter a list of protocols to only those with valid configs and available binaries
    
    Returns a list of protocol names that are ready to use.
    Skips protocols with invalid configs or missing binaries.
    """
    available: list[str] = []
    for name in order:
        proto = PROTOCOLS.get(name)
        if not proto:
            log_event(f"Unknown protocol: {name}", "error")
            continue
        if not validate_protocol_config(name):
            log_event(f"Skipping {name}: invalid or missing configuration.", "error")
            continue
        if not ensure_binary(proto["cmd"]):
            log_event(f"Skipping {name}: required tool {proto['cmd']} is unavailable.", "error")
            continue
        available.append(name)
    return available


def stop_xray() -> None:
    """Stop the XRay process if it's running
    
    Reads the PID from file, sends SIGTERM if process exists, and cleans up PID file.
    Only stops processes we started (safe shutdown).
    """
    pid = read_pid_file(XRAY_PID_FILE)
    if pid and is_process_running(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            log_event(f"Stopped XRay process pid={pid}.")
        except Exception as e:
            log_event(f"Failed to stop XRay pid={pid}: {e}", "error")
    else:
        log_event("No managed XRay process found to stop. Skipping unsafe shutdown.", "info")
    remove_pid_file(XRAY_PID_FILE)
# ─── NETWORK UTILITIES ────────────────────────────────────────────────────────

def get_latency(iface: str) -> str:
    """Measure latency (ping) to CHECK_IP via specific interface
    
    Returns average latency in ms, or "N/A" if failed.
    Uses ping with interface binding.
    """
    try:
        output = subprocess.check_output(
            ["ping", "-c", "3", "-i", "0.2", "-W", "2", "-I", iface, CHECK_IP],
            text=True, stderr=subprocess.DEVNULL
        )
        match = re.search(r"(\d+\.\d+)/(\d+\.\d+)/(\d+\.\d+)", output)
        if match:
            return f"{match.group(2)} ms"
    except Exception:
        pass
    return "N/A"

def is_internet_up(iface: str) -> bool:
    """Check internet connectivity by pinging CHECK_IP via interface
    
    Returns True if ping succeeds, False otherwise.
    Used for basic connectivity testing.
    """
    try:
        subprocess.check_call(
            ["ping", "-c", "1", "-W", "2", "-I", iface, CHECK_IP],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return True
    except subprocess.CalledProcessError:
        return False


def _route_uses_iface(dest_ip: str, iface: str) -> bool:
    """
    Checks whether Linux routing would send dest_ip via iface.
    
    Uses 'ip route get' to determine which interface would be used for a destination.
    """
    try:
        out = subprocess.check_output(["ip", "-4", "route", "get", dest_ip], text=True)
    except Exception:
        return False
    return bool(re.search(rf"\bdev\s+{re.escape(iface)}\b", out))


def _route_get(dest_ip: str) -> str:
    """Get the full routing information for a destination IP"""
    try:
        return subprocess.check_output(["ip", "-4", "route", "get", dest_ip], text=True).strip()
    except Exception as e:
        return f"<route get failed: {e}>"


def _tcp_check(dest_ip: str, port: int, timeout_s: float = 2.0) -> tuple[bool, str]:
    """
    Connectivity check that works even when ICMP is blocked or not supported by the tunnel.
    
    Uses TCP connection to port (usually 443) instead of ping.
    Returns (success, error_message)
    """
    try:
        with socket.create_connection((dest_ip, port), timeout=timeout_s):
            return True, ""
    except Exception as e:
        return False, str(e)


def get_public_ip(timeout_s: float = 3.0) -> str:
    """
    Best-effort public IP lookup with short timeouts.
    
    Tries multiple IP lookup services to get the current public IP address.
    Used for status reporting.
    """
    candidates = [
        ["curl", "-fsS", "--max-time", str(timeout_s), "https://api.ipify.org"],
        ["curl", "-fsS", "--max-time", str(timeout_s), "https://ifconfig.me/ip"],
    ]
    for cmd in candidates:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            ip = (res.stdout or "").strip()
            if res.returncode == 0 and re.match(r"^\d+\.\d+\.\d+\.\d+$", ip):
                return ip
        except Exception:
            continue
    return "N/A"

def find_best_mtu(iface: str) -> int:
    """Find the optimal MTU for an interface by testing ping with different sizes
    
    Starts from MTU_START (1492) and decreases until ping succeeds.
    Prevents packet fragmentation on mobile carriers.
    """
    log_event(f"🔍 Optimizing MTU for {iface}...")
    for mtu in range(MTU_START, MTU_MIN - 1, -20):
        try:
            # ping -M do: don't fragment | -s: payload size (MTU - 28)
            subprocess.check_call(
                ["ping", "-c", "1", "-W", "1", "-M", "do", "-s", str(mtu - 28), CHECK_IP],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            log_event(f"✅ Optimal MTU found: {mtu}")
            return mtu
        except subprocess.CalledProcessError:
            continue
    return MTU_MIN

def apply_mtu(iface: str, mtu: int):
    """Apply the optimal MTU setting to an interface"""
    ok, err = run_cmd(["ip", "link", "set", "dev", iface, "mtu", str(mtu)])
    if not ok:
        log_event(f"⚠️  Failed to set MTU on {iface}: {err}", "error")

def run_cmd(command: list) -> tuple[bool, str]:
    """Run a shell command and return (success, error_message)"""
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        return True, ""
    except subprocess.CalledProcessError as e:
        return False, e.stderr.strip()

def _list_links() -> set[str]:
    """Get a set of all network interface names currently on the system"""
    try:
        out = subprocess.check_output(["ip", "-o", "link", "show"], text=True)
    except Exception:
        return set()
    names: set[str] = set()
    for line in out.splitlines():
        # "33: xray0: <...>"
        m = re.match(r"^\d+:\s+([^:]+):\s+", line)
        if m:
            names.add(m.group(1))
    return names


def _detect_xray_iface(before: set[str]) -> str | None:
    """
    Best-effort detection of the interface XRay created.
    
    XRay may ignore the configured interfaceName and create xray0, xray1, etc.
    Compares interface list before/after XRay start to find the new one.
    """
    after = _list_links()
    created = list(after - before)
    # Prefer xray* if present, otherwise pick the first new link.
    created.sort()
    for name in created:
        if name.startswith("xray"):
            return name
    return created[0] if created else None


def _read_xray_server_ip(xray_conf_path: str) -> str | None:
    """Extract the VLESS server IP address from XRay config file
    
    Needed for routing: keep server reachable outside the tunnel.
    """
    try:
        with open(xray_conf_path, "r", encoding="utf-8") as f:
            conf = json.load(f)
    except Exception:
        return None

    for ob in conf.get("outbounds", []):
        if ob.get("protocol") != "vless":
            continue
        settings = ob.get("settings") or {}
        for vnext in settings.get("vnext", []):
            addr = vnext.get("address")
            if isinstance(addr, str) and addr:
                return addr
    return None


def _read_xray_tun_cidr(xray_conf_path: str) -> str | None:
    """
    Extract the first IPv4 CIDR from the tun inbound `settings.address`.
    
    Example: "10.0.0.2/24" - needed to assign IP to the kernel interface.
    """
    try:
        with open(xray_conf_path, "r", encoding="utf-8") as f:
            conf = json.load(f)
    except Exception:
        return None

    for ib in conf.get("inbounds", []):
        if ib.get("protocol") != "tun":
            continue
        settings = ib.get("settings") or {}
        addrs = settings.get("address") or []
        if isinstance(addrs, list):
            for a in addrs:
                if isinstance(a, str) and re.match(r"^\d+\.\d+\.\d+\.\d+/\d+$", a):
                    return a
    return None


def _read_xray_port(xray_conf_path: str) -> int | None:
    """Extract the port from VLESS outbound configuration"""
    try:
        with open(xray_conf_path, "r", encoding="utf-8") as f:
            conf = json.load(f)
    except Exception:
        return None

    for ob in conf.get("outbounds", []):
        if ob.get("protocol") != "vless":
            continue
        settings = ob.get("settings") or {}
        for vnext in settings.get("vnext", []):
            port = vnext.get("port")
            if isinstance(port, int):
                return port
    return None


def _iface_has_ipv4(iface: str) -> bool:
    """Check if an interface has an IPv4 address assigned"""
    try:
        out = subprocess.check_output(["ip", "-4", "addr", "show", "dev", iface], text=True)
    except Exception:
        return False
    return bool(re.search(r"\binet\s+\d+\.\d+\.\d+\.\d+/\d+\b", out))


def _ensure_iface_ipv4_from_xray_conf(iface: str, xray_conf_path: str) -> bool:
    """
    Best-effort: ensure the kernel interface has the IPv4 address configured in XRay tun inbound.
    
    Some XRay tun modes create the interface but don't assign the IP in the OS.
    We need to manually assign it for routing to work.
    Returns True if IPv4 is present after the attempt.
    """
    cidr = _read_xray_tun_cidr(xray_conf_path)
    if not cidr:
        log_event(f"⚠️  Could not read tun IPv4 from {xray_conf_path}", "error")
        return False

    log_event(f"🛠️  Ensuring {iface} has IPv4 {cidr}...")

    # Race/ordering: xray may create the link slightly later; also some setups
    # may remove the address after we add it. Try a few times and verify.
    for _ in range(10):
        if _iface_has_ipv4(iface):
            try:
                out4 = subprocess.check_output(["ip", "-4", "addr", "show", "dev", iface], text=True)
            except Exception as e:
                out4 = f"<failed to read ipv4 addr: {e}>"
            log_event(f"✅ {iface} IPv4 present:\n{out4}")
            return True

        run_cmd(["ip", "link", "set", "dev", iface, "up"])
        ok_a, err_a = run_cmd(["ip", "addr", "replace", cidr, "dev", iface])
        if not ok_a:
            log_event(f"⚠️  Failed to assign {cidr} to {iface}: {err_a}", "error")

        time.sleep(0.2)

    # Final diagnostic dump
    try:
        out4 = subprocess.check_output(["ip", "-4", "addr", "show", "dev", iface], text=True)
    except Exception as e:
        out4 = f"<failed to read ipv4 addr: {e}>"
    log_event(f"⚠️  {iface} still has no IPv4. ip -4 addr show:\n{out4}", "error")
    return False


def _get_default_gateway() -> str | None:
    """
    Returns the current IPv4 default gateway (if any).
    
    Needed for VLESS routing: keep server reachable via original gateway.
    """
    ok, out = True, ""
    try:
        out = subprocess.check_output(["ip", "-4", "route", "show", "default"], text=True)
    except Exception:
        ok = False
    if not ok or not out.strip():
        return None

    # Example: "default via 192.168.1.1 dev wlan0 proto dhcp metric 600"
    m = re.search(r"\bvia\s+(\d+\.\d+\.\d+\.\d+)\b", out)
    return m.group(1) if m else None


def _tail_text_file(path: str, max_bytes: int = 8192) -> str:
    """Read the last max_bytes of a text file (for log tailing)"""
    try:
        with open(path, "rb") as f:
            try:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - max_bytes), os.SEEK_SET)
            except Exception:
                f.seek(0, os.SEEK_SET)
            data = f.read().decode("utf-8", errors="replace")
            return data.strip()
    except Exception:
        return ""


def _is_xray_running() -> bool:
    """Check if any XRay process is running using pgrep"""
    return subprocess.run(["pgrep", "-x", "xray"], capture_output=True).returncode == 0


def _find_active_xray_iface() -> str | None:
    """
    XRay may ignore `interfaceName` and create `xray0`, `xray1`, ...
    
    Detect the active one by checking which `xray*` link is used by routing for CHECK_IP.
    """
    links = sorted([n for n in _list_links() if n.startswith("xray")])
    if not links:
        return None

    for n in links:
        if _route_uses_iface(CHECK_IP, n):
            return n

    # Fallback: if there's exactly one xray interface, assume it's the active one.
    if len(links) == 1:
        return links[0]
    return None


def _internet_ok(name: str, proto: dict) -> bool:
    """Check if internet connectivity works through the given VPN protocol"""
    if proto.get("cmd") == "xray":
        ok, _ = _tcp_check(CHECK_IP, 443, timeout_s=2.0)
        return ok
    return is_internet_up(proto["iface"])


def get_active_vpn():
    """Detect which VPN protocol is currently active
    
    Returns (protocol_name, protocol_config) or (None, None)
    Checks kernel interfaces first, then XRay processes.
    """
    # Detect kernel-interface VPNs first.
    for name in [n for n in FALLBACK_ORDER if n != "vless"]:
        proto = PROTOCOLS[name]
        ok, _ = run_cmd(["ip", "link", "show", proto["iface"]])
        if ok:
            return name, proto

    # Detect XRay VLESS Reality.
    if "vless" in PROTOCOLS and _is_xray_running():
        iface = _find_active_xray_iface()
        if iface:
            proto = dict(PROTOCOLS["vless"])
            proto["iface"] = iface
            return "vless", proto
    return None, None

def stop_all():
    """Brings down all VPN interfaces in config."""
    stop_xray()
    for proto in PROTOCOLS.values():
        if proto["cmd"] != "xray":
            run_cmd([proto["cmd"], "down", str(proto["conf"])])

# ─── CORE LOGIC ───────────────────────────────────────────────────────────────

def connect(order: list[str] | None = None):
    """Main connection function with automatic fallback and adaptive mutation.

    Tries protocols in order until one succeeds. The engine consults historic
    performance per network context and will generate mutated config variants
    when the current best-known config does not succeed.
    """
    stop_all()
    log_event("--- New Connection Session Started ---")

    brain = Brain()
    network_id = brain.detect_network_id()
    order = order or FALLBACK_ORDER
    order = brain.recommend_protocol_order(network_id, order)

    log_event(f"🧠 Network profile: {network_id}")
    log_event(f"🧭 Adaptive fallback order: {', '.join(order)}")

    order = filter_available_protocols(order)
    if not order:
        log_event("No valid protocols are available. Check your configs and required binaries.", "error")
        return None

    for name in order:
        proto = dict(PROTOCOLS[name])
        mutator = ConfigMutator(name)
        best_hash = brain.best_config_hash(name, network_id)

        if best_hash:
            best_path = mutator.find_variant_path(best_hash)
            if best_path:
                proto["conf"] = best_path
                log_event(f"🧠 Reusing best-known variant for {name}: {best_hash[:8]}")
            else:
                log_event(f"🧠 Best historic {name} variant {best_hash[:8]} is missing locally. Using default config.")

        tried_mutation = False
        for phase in range(2):
            if phase == 1:
                if tried_mutation:
                    break
                try:
                    mutation = mutator.generate_random_variant()
                    proto["conf"] = mutation.path
                    log_event(f"🧬 Generated mutant variant for {name}: {mutation.config_hash[:8]}")
                    tried_mutation = True
                except Exception as e:
                    log_event(f"⚠️ Could not generate variant for {name}: {e}", "error")
                    break

            attempts = WG_ATTEMPTS if name == "wg" else 1
            for i in range(1, attempts + 1):
                start_time = time.time()
                retry_str = f" [Attempt {i}/{attempts}]" if attempts > 1 else ""
                log_event(f"🚀{retry_str} Trying {proto['label']}...")

                config_hash = compute_config_hash(proto["conf"])
                port = _read_xray_port(str(proto["conf"])) if name == "vless" else None
                mtu = MTU_MIN
                success = False
                latency = "N/A"

                def record_metrics(success: bool, mtu: int, latency: str, duration: float, error_msg: str = ""):
                    log_connection_metrics(
                        name,
                        config_hash,
                        mtu,
                        latency,
                        success,
                        duration,
                        port,
                        error_msg,
                        network_id,
                    )

                if proto["cmd"] == "xray":
                    before_links = _list_links()
                    with open(XRAY_LOG_FILE, "ab", buffering=0) as xray_log:
                        xray_proc = subprocess.Popen(
                            [proto["cmd"], "run", "-c", str(proto["conf"])],
                            stdout=xray_log,
                            stderr=xray_log,
                        )
                    write_pid_file(XRAY_PID_FILE, xray_proc.pid)

                    deadline = time.time() + max(CONNECT_WAIT.get(name, 5), 15)
                    while time.time() < deadline:
                        if xray_proc.poll() is not None:
                            log_event(
                                f"❌ XRay exited early (code {xray_proc.returncode}). Check {XRAY_LOG_FILE}",
                                "error",
                            )
                            remove_pid_file(XRAY_PID_FILE)
                            record_metrics(False, MTU_MIN, "N/A", time.time() - start_time, f"XRay exited early with code {xray_proc.returncode}")
                            break

                        ok, _ = run_cmd(["ip", "link", "show", proto["iface"]])
                        if ok:
                            break
                        time.sleep(0.2)

                    ok, _ = run_cmd(["ip", "link", "show", proto["iface"]])
                    if not ok:
                        detected = _detect_xray_iface(before_links)
                        if detected:
                            proto = dict(proto)
                            proto["iface"] = detected
                            ok = True

                    if not ok:
                        tail = _tail_text_file(str(XRAY_LOG_FILE))
                        log_event(f"❌ {proto['iface']} not created by XRay. Check {XRAY_LOG_FILE}", "error")
                        if tail:
                            log_event(f"--- xray.log (tail) ---\n{tail}\n--- end xray.log ---", "error")
                        record_metrics(False, MTU_MIN, "N/A", time.time() - start_time, "interface not created")
                        continue

                    if not _iface_has_ipv4(proto["iface"]):
                        _ensure_iface_ipv4_from_xray_conf(proto["iface"], str(proto["conf"]))

                    try:
                        gateway = _get_default_gateway()
                        if not gateway:
                            raise RuntimeError("Could not determine default gateway")

                        server_ip = _read_xray_server_ip(str(proto["conf"])) or "151.245.216.157"
                        ok1, err1 = run_cmd(["ip", "route", "replace", server_ip, "via", gateway])
                        ok2, err2 = run_cmd(["ip", "route", "replace", "default", "dev", proto["iface"], "metric", "50"])
                        if not ok1 or not ok2:
                            raise RuntimeError("; ".join([e for e in [err1, err2] if e]))

                        log_event(f"📍 route get {CHECK_IP}: {_route_get(CHECK_IP)}")
                        if not _route_uses_iface(CHECK_IP, proto["iface"]):
                            raise RuntimeError(f"Default route does not use {proto['iface']} for {CHECK_IP}")

                        success = True
                    except Exception as e:
                        log_event(f"Routing error: {e}", "error")
                        record_metrics(False, MTU_MIN, "N/A", time.time() - start_time, str(e))
                        success = False
                else:
                    success, err = run_cmd([proto["cmd"], "up", str(proto["conf"])])
                    if not success:
                        record_metrics(False, MTU_MIN, "N/A", time.time() - start_time, err)
                        continue

                if success:
                    time.sleep(2)
                    if proto["cmd"] == "xray":
                        ok_up, err = _tcp_check(CHECK_IP, 443, timeout_s=2.0)
                        if not ok_up:
                            log_event(f"⚠️  TCP check to {CHECK_IP}:443 failed: {err}", "error")
                    else:
                        ok_up = is_internet_up(proto["iface"])

                    if ok_up:
                        log_event(f"✅ {proto['label']} active.")
                        best_mtu = find_best_mtu(proto["iface"])
                        apply_mtu(proto["iface"], best_mtu)
                        latency = get_latency(proto["iface"]) if proto["cmd"] != "xray" else "N/A"
                        record_metrics(True, best_mtu, latency, time.time() - start_time)
                        return name

                    log_event(f"⚠️  {proto['label']} failed to pass traffic.", "error")
                    record_metrics(False, MTU_MIN, "N/A", time.time() - start_time, "traffic check failed")
                    stop_all()

            if phase == 0 and not tried_mutation:
                continue
            break

    log_event("❌ Critical: All protocols failed.", "error")
    return None

def daemon_mode():
    """Run VPN-Agent in daemon mode for auto-recovery
    
    Monitors connection health and automatically reconnects if it drops.
    Also tries to recover to primary protocol (WG) when possible.
    Uses PID locking to prevent multiple daemons.
    """
    existing_pid = read_pid_file(DAEMON_PID_FILE)
    if existing_pid and is_process_running(existing_pid):
        log_event(f"Daemon already running with pid={existing_pid}.", "error")
        return

    write_pid_file(DAEMON_PID_FILE, os.getpid())
    log_event(f"🏃 Daemon started: Recovery check every {RECOVERY_CHECK}s")
    last_recovery_check = 0.0
    try:
        while True:
            time.sleep(10)
            name, proto = get_active_vpn()

            if not name or not _internet_ok(name, proto):
                log_event("🔴 Connection lost! Reconnecting...")
                connect()
                continue

            # If on fallback (AWG/VLESS), try to revert to primary (WG)
            if name != FALLBACK_ORDER[0]:
                now = time.time()
                if now - last_recovery_check >= RECOVERY_CHECK:
                    last_recovery_check = now
                    if connect() == FALLBACK_ORDER[0]:
                        log_event("♻️  Recovered to Standard WireGuard!")
    finally:
        remove_pid_file(DAEMON_PID_FILE)


def show_status():
    """Display comprehensive VPN status information
    
    Shows binary availability, config presence, connection status,
    and detailed info about active VPN (interface, IP, latency, MTU).
    """
    name, proto = get_active_vpn()
    binaries_ok = all(find_binary(pinfo["cmd"]) for pinfo in PROTOCOLS.values())
    configs_ok = all(os.path.exists(pinfo["conf"]) for pinfo in PROTOCOLS.values())
    connection_ok = bool(proto and _internet_ok(name, proto))

    print("[{}] Binaries: {}".format("✔" if binaries_ok else " ", "OK" if binaries_ok else "Issue detected"))
    print("[{}] Configs: {}".format("✔" if configs_ok else " ", "Found" if configs_ok else "Missing files"))
    print("[{}] Connection: {}".format("✔" if connection_ok else " ", "Active" if connection_ok else "Inactive"))
    print("-" * 40)

    if proto is None:
        print("Status: 🔴 Disconnected")
        return

    latency = get_latency(proto["iface"]) if proto.get("cmd") != "xray" else "N/A"
    public_ip = get_public_ip()
    traffic = "OK" if _internet_ok(name, proto) else "FAIL"
    
    # FETCH CURRENT MTU
    current_mtu = "Unknown"
    try:
        # Pulling MTU directly from the interface attributes
        output = subprocess.check_output(["ip", "link", "show", proto["iface"]], text=True)
        mtu_match = re.search(r"mtu (\d+)", output)
        if mtu_match:
            current_mtu = mtu_match.group(1)
    except Exception:
        pass

    print(f"Status:     🟢 Connected ({name}) — {proto['label']}")
    print(f"Interface:  {proto['iface']}")
    print(f"Public IP:  {public_ip}")
    print(f"Traffic:    {traffic}")
    print(f"Latency:    ⚡ {latency}")
    print(f"MTU:        📦 {current_mtu}")
    print("-" * 40)
    
    if proto["cmd"] != "xray":
        result = subprocess.run([proto["show_cmd"], "show", proto["iface"]], capture_output=True, text=True)
        print(result.stdout.strip())

# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

def main():
    """Main entry point for the VPN-Agent CLI
    
    Parses command-line arguments and dispatches to appropriate functions.
    Requires root privileges for network operations.
    """
    if os.getuid() != 0:
        print("❌ Please run with sudo.")
        sys.exit(1)

    parser = argparse.ArgumentParser(description=f"VPN-Agent v{VERSION}")
    parser.add_argument("-v", "--version", action="version", version=f"VPN-Agent {VERSION}")
    parser.add_argument(
        "command",
        choices=["connect", "disconnect", "status", "daemon", "up", "down"],
        help="Use connect/disconnect (up/down are deprecated aliases).",
    )
    parser.add_argument(
        "--protocol",
        choices=list(PROTOCOLS.keys()),
        help="Force a specific protocol (skips fallback order)",
    )
    parser.add_argument(
        "--proto",
        dest="protocol",
        choices=list(PROTOCOLS.keys()),
        help=argparse.SUPPRESS,
    )
    
    args = parser.parse_args()

    if args.command in ("connect", "up"):
        if args.protocol:
            connect([args.protocol])
        else:
            connect()
    elif args.command in ("disconnect", "down"):
        stop_all()
        log_event("🛑 VPN Disconnected.")
    elif args.command == "status":
        show_status()
    elif args.command == "daemon":
        daemon_mode()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Stopped.")