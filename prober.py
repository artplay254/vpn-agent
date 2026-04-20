#!/usr/bin/env python3

import subprocess
from typing import Optional, Tuple

try:
    from rich.status import Status
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

PING_TIMEOUT = 2.0
PING_COUNT = 1
PING_WAIT = 1

_PERMISSION_ERRORS = (
    "operation not permitted",
    "permission denied",
    "cannot open socket",
    "socket: operation not permitted",
    "icmp open socket",
)
_NETWORK_ERRORS = (
    "network is unreachable",
    "destination host unreachable",
    "no route to host",
    "temporary failure in name resolution",
)
_FRAGMENT_ERRORS = (
    "frag needed",
    "need to fragment",
    "message too long",
    "packet needs to be fragmented",
)


def _normalize_output(stdout: str | None, stderr: str | None) -> str:
    return "\n".join([line for line in ((stdout or "").splitlines() + (stderr or "").splitlines()) if line]).strip().lower()


def _parse_ping_result(output: str, returncode: int) -> Tuple[bool, Optional[str]]:
    if returncode == 0:
        return True, None

    if any(token in output for token in _PERMISSION_ERRORS):
        raise PermissionError(
            "Ping requires elevated privileges or CAP_NET_RAW. Run as root or allow ping capabilities."
        )

    if any(token in output for token in _NETWORK_ERRORS):
        raise RuntimeError(
            "Network appears to be down or unreachable. Check local network connectivity before probing MTU."
        )

    if any(token in output for token in _FRAGMENT_ERRORS):
        return False, "Fragmentation needed"

    if "100% packet loss" in output:
        return False, "No response from target; packet loss detected"

    return False, "Ping failed"


def _ping_payload(target_host: str, payload_size: int) -> Tuple[bool, Optional[str]]:
    cmd = [
        "ping",
        "-c",
        str(PING_COUNT),
        "-W",
        str(PING_WAIT),
        "-M",
        "do",
        "-s",
        str(payload_size),
        target_host,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=PING_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError("ping command not found on PATH") from exc
    except subprocess.TimeoutExpired:
        return False, "Ping timed out"

    output = _normalize_output(result.stdout, result.stderr)
    return _parse_ping_result(output, result.returncode)


def probe_mtu(target_host: str = "1.1.1.1", min_mtu: int = 1200, max_mtu: int = 1500) -> int:
    """Perform a binary search to discover the highest MTU that can be sent without fragmentation.

    Uses `ping -M do` with payload size = mtu - 28.
    If the probe command requires permissions or the network is unreachable, raises an exception.
    """
    if min_mtu < 68 or max_mtu < min_mtu:
        raise ValueError("Invalid MTU range")

    def _search() -> int:
        best_mtu: Optional[int] = None
        low = min_mtu
        high = max_mtu

        while low <= high:
            mtu = (low + high) // 2
            payload_size = mtu - 28
            if payload_size < 0:
                raise ValueError("Computed payload size is too small")

            success, reason = _ping_payload(target_host, payload_size)
            if success:
                best_mtu = mtu
                low = mtu + 1
            else:
                high = mtu - 1

        if best_mtu is None:
            raise RuntimeError(
                f"Could not discover a viable MTU to {target_host}. Verify network connectivity and ping privileges."
            )

        return best_mtu

    if RICH_AVAILABLE:
        with Status("🔍 Scanning network MTU limits...", spinner="dots"):
            return _search()

    return _search()
