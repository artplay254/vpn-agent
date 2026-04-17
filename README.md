# 🛡️ VPN-Agent

A lightweight, intelligent Python CLI for Linux (Arch/Ubuntu) that manages a multi-layer defense against DPI. It automatically navigates through **WireGuard**, **AmneziaWG**, and **VLESS Reality** to ensure you stay connected.

## ✨ Features

- **Smart Protocol Fallback**: The agent follows a tactical connection sequence to beat blocks:
    1. **Standard WireGuard**: Attempts to connect (3 retries).
    2. **AmneziaWG**: Falls back to obfuscated headers if standard WG is handshake-blocked.
    3. **VLESS + Reality**: The final "stealth" layer for extreme DPI environments, masking traffic as standard HTTPS.
- **Auto-Recovery (Daemon)**: Monitors connection health in the background. If the network block is lifted, the daemon can automatically revert to standard WireGuard to minimize overhead.
- **Self-Healing Infrastructure**: 
    - **Adaptive MTU Management**: Automatically forces optimized MTU settings (e.g., 1400) during the connection phase to prevent packet fragmentation on mobile carriers.
    - **Dynamic Interface Detection**: Real-time detection of TUN devices (like `xray0`) to handle non-standard naming conventions.
    - **Kernel-Level IPv4 Force**: Ensures TUN interfaces are properly initialized with an IP and set to `UP` state on Arch Linux.
- **Advanced Monitoring**: 
    - **TCP Handshake Validation**: Verifies real-world connectivity via `1.1.1.1:443` instead of unreliable ICMP pings.
    - **Dual Logging**: Separate streams for `agent.log` (management) and `xray.log` (core).
- **English-First CLI**: Professional interface designed for developers.

## 🚀 Installation

### 1. Server Side (Ubuntu 24.04)
Run the automated setup script to configure WireGuard/AmneziaWG and install the Xray-core Reality stack:
```bash
wget https://raw.githubusercontent.com/artplay254/vpn-agent/main/setup_server.sh
chmod +x setup_server.sh
sudo ./setup_server.sh
```

### 2. Client Side (Arch/Linux)
Clone the repository:
```bash
git clone https://github.com/artplay254/vpn-agent ~/.config/vpn-agent
cd ~/.config/vpn-agent
```
1. Add your `client_wg.conf` and `client_awg.conf`.
2. Configure `vless.json` based on the provided `vless.json.example`.
3. (Arch Users) `sudo setcap "cap_net_admin,cap_net_bind_service+ep" $(which xray)`

### 3. Quick Alias
Add this to your ~/.zshrc or ~/.bashrc:
```bash
alias vpn='sudo python3 ~/.config/vpn-agent/vpn_cli.py'
```

## 🛠 Usage

### Commands
- **Connect (preferred)**: `vpn connect`
- **Disconnect (preferred)**: `vpn disconnect`
- **Status**: `vpn status`
- **Daemon**: `vpn daemon`

### Deprecated aliases (still supported)
- **Connect alias**: `vpn up`
- **Disconnect alias**: `vpn down`

### Options
- **Force protocol**: `--protocol {wg,awg,vless}`
  - **Force WireGuard**: `vpn connect --protocol wg`
  - **Force AmneziaWG**: `vpn connect --protocol awg`
  - **Force VLESS (XRay)**: `vpn connect --protocol vless`

### Version/help
- **Help**: `vpn -h`
- **Version**: `vpn --version`

## 🔜 Roadmap

- **Multi-Server Support**: Quick-switch functionality for diverse VPS locations (e.g., Estonia 🇪🇪, Netherlands 🇳🇱).
- **TUI Dashboard**: A terminal-based visual monitor for traffic and protocol health.
- **Automated MTU Probing**: Future enhancement to dynamically probe the best MTU per network session.

## 🌟 Support
If you find this tool useful, please **leave a star**! Every star is huge motivation for a 15-year-old dev on a mission to Europe. 🚀🦾
