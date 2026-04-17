import subprocess
import time
import os
import sys
import argparse
import re
import logging

import json
import socket

from config import (
    VERSION, PROTOCOLS, FALLBACK_ORDER, CHECK_IP, 
    CONNECT_WAIT, WG_ATTEMPTS, RECOVERY_CHECK, LOG_FILE,
    MTU_START, MTU_MIN,
    XRAY_LOG_FILE,
)

# ─── SETUP LOGGING ────────────────────────────────────────────────────────────
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def log_event(message, level="info"):
    """Sync output to terminal and agent.log"""
    print(message)
    if level == "error":
        logging.error(message)
    else:
        logging.info(message)

# ─── NETWORK UTILITIES ────────────────────────────────────────────────────────

def get_latency(iface: str) -> str:
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
    """
    try:
        out = subprocess.check_output(["ip", "-4", "route", "get", dest_ip], text=True)
    except Exception:
        return False
    return bool(re.search(rf"\bdev\s+{re.escape(iface)}\b", out))


def _route_get(dest_ip: str) -> str:
    try:
        return subprocess.check_output(["ip", "-4", "route", "get", dest_ip], text=True).strip()
    except Exception as e:
        return f"<route get failed: {e}>"


def _tcp_check(dest_ip: str, port: int, timeout_s: float = 2.0) -> tuple[bool, str]:
    """
    Connectivity check that works even when ICMP is blocked or not supported by the tunnel.
    """
    try:
        with socket.create_connection((dest_ip, port), timeout=timeout_s):
            return True, ""
    except Exception as e:
        return False, str(e)


def get_public_ip(timeout_s: float = 3.0) -> str:
    """
    Best-effort public IP lookup with short timeouts.
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
    ok, err = run_cmd(["ip", "link", "set", "dev", iface, "mtu", str(mtu)])
    if not ok:
        log_event(f"⚠️  Failed to set MTU on {iface}: {err}", "error")

def run_cmd(command: list) -> tuple[bool, str]:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        return True, ""
    except subprocess.CalledProcessError as e:
        return False, e.stderr.strip()

def _list_links() -> set[str]:
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
    On some systems XRay ignores `interfaceName` and creates `xray0`, `xray1`, ...
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
    Example: "10.0.0.2/24"
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


def _iface_has_ipv4(iface: str) -> bool:
    try:
        out = subprocess.check_output(["ip", "-4", "addr", "show", "dev", iface], text=True)
    except Exception:
        return False
    return bool(re.search(r"\binet\s+\d+\.\d+\.\d+\.\d+/\d+\b", out))


def _ensure_iface_ipv4_from_xray_conf(iface: str, xray_conf_path: str) -> bool:
    """
    Best-effort: ensure the kernel interface has the IPv4 address configured in XRay tun inbound.
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
    return subprocess.run(["pgrep", "-x", "xray"], capture_output=True).returncode == 0


def _find_active_xray_iface() -> str | None:
    """
    XRay may ignore `interfaceName` and create `xray0`, `xray1`, ...
    Detect the active one by checking which `xray*` link is used by routing.
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
    if proto.get("cmd") == "xray":
        ok, _ = _tcp_check(CHECK_IP, 443, timeout_s=2.0)
        return ok
    return is_internet_up(proto["iface"])


def get_active_vpn():
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
    for proto in PROTOCOLS.values():
        if proto["cmd"] == "xray":
            # Be strict to avoid killing unrelated processes.
            subprocess.run(["pkill", "-x", "xray"], capture_output=True)
        else:
            run_cmd([proto["cmd"], "down", str(proto["conf"])])

# ─── CORE LOGIC ───────────────────────────────────────────────────────────────

def connect(order: list[str] | None = None):
    stop_all()
    log_event("--- New Connection Session Started ---")
    
    order = order or FALLBACK_ORDER
    for name in order:
        proto = PROTOCOLS[name]
        attempts = WG_ATTEMPTS if name == "wg" else 1
        
        for i in range(1, attempts + 1):
            retry_str = f" [Attempt {i}/{attempts}]" if attempts > 1 else ""
            log_event(f"🚀{retry_str} Trying {proto['label']}...")
            
            if proto["cmd"] == "xray":
                # 1. Start Xray in background
                before_links = _list_links()
                with open(XRAY_LOG_FILE, "ab", buffering=0) as xray_log:
                    xray_proc = subprocess.Popen(
                        [proto["cmd"], "run", "-c", str(proto["conf"])],
                        stdout=xray_log,
                        stderr=xray_log,
                    )

                # 2. Wait for tun0 to initialize (or xray to crash)
                deadline = time.time() + max(CONNECT_WAIT.get(name, 5), 15)
                while time.time() < deadline:
                    if xray_proc.poll() is not None:
                        log_event(
                            f"❌ XRay exited early (code {xray_proc.returncode}). "
                            f"Check {XRAY_LOG_FILE}",
                            "error",
                        )
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
                    log_event(
                        f"❌ {proto['iface']} not created by XRay. Check {XRAY_LOG_FILE}",
                        "error",
                    )
                    if tail:
                        log_event(f"--- xray.log (tail) ---\n{tail}\n--- end xray.log ---", "error")
                    success = False
                    continue
                
                # Ensure the kernel has an IPv4 on the created link; some XRay tun modes
                # bring the interface up without assigning the address in the OS.
                if not _iface_has_ipv4(proto["iface"]):
                    _ensure_iface_ipv4_from_xray_conf(proto["iface"], str(proto["conf"]))

                # 3. MANUALLY FIX ROUTING FOR VLESS
                try:
                    gateway = _get_default_gateway()
                    if not gateway:
                        raise RuntimeError("Could not determine default gateway")

                    # Keep the VLESS server reachable outside the tunnel.
                    server_ip = _read_xray_server_ip(str(proto["conf"])) or "151.245.216.157"
                    
                    # Idempotent route updates (avoid "File exists" on reconnects).
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
                    success = False
            else:
                success, _ = run_cmd([proto["cmd"], "up", str(proto["conf"])])
            
            if success:
                time.sleep(2) # Stabilization
                if proto["cmd"] == "xray":
                    # For XRay tun, binding ping to the iface is unreliable on some setups.
                    # Also, some XRay tun modes don't forward ICMP (so ping can fail while TCP works).
                    ok_up, err = _tcp_check(CHECK_IP, 443, timeout_s=2.0)
                    if not ok_up:
                        log_event(f"⚠️  TCP check to {CHECK_IP}:443 failed: {err}", "error")
                else:
                    ok_up = is_internet_up(proto["iface"])

                if ok_up:
                    log_event(f"✅ {proto['label']} active.")
                    best_mtu = find_best_mtu(proto["iface"])
                    apply_mtu(proto["iface"], best_mtu)
                    return name
                
                log_event(f"⚠️  {proto['label']} failed to pass traffic.", "error")
                stop_all()
            
    log_event("❌ Critical: All protocols failed.", "error")
    return None

def daemon_mode():
    log_event(f"🏃 Daemon started: Recovery check every {RECOVERY_CHECK}s")
    last_recovery_check = 0.0
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

def show_status():
    name, proto = get_active_vpn()
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