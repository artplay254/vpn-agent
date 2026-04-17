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
    - **Dynamic Interface Detection**: Real-time detection of TUN devices (like `xray0`/`xray1`) to handle Xray's dynamic naming conventions.
    - **Kernel-Level IPv4 Force**: Ensures TUN interfaces are properly initialized with an IP and set to `UP` state on Arch Linux.
- **Advanced Monitoring & Status**: 
    - **Real-Time Visibility**: `status` now reports the active protocol, the specific interface name, and your current **Public IP**.
    - **Traffic Validation**: Separates process status from data flow with a **Traffic: OK/FAIL** indicator verified via TCP handshakes to `1.1.1.1:443`.
    - **Dual Logging**: Separate streams for `agent.log` (management logic) and `xray.log` (core output).
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
Add this to your `~/.zshrc` or `~/.bashrc`:
```bash
alias vpn='sudo python3 ~/.config/vpn-agent/vpn_cli.py'
```

## 🛠 Usage

### Commands
- **Connect**: `vpn connect` (Successor to `up`)
- **Disconnect**: `vpn disconnect` (Successor to `down`)
- **Status**: `vpn status` (Now includes Public IP and Traffic verification)
- **Daemon**: `vpn daemon`

### Deprecated Aliases (Still Supported)
- `vpn up` -> `vpn connect`
- `vpn down` -> `vpn disconnect`

### Options
- **Force Protocol**: `--protocol {wg,awg,vless}`
  - Example: `vpn connect --protocol vless`
- **Help**: `vpn -h`
- **Version**: `vpn --version` (v0.2.1)

## 🔜 Roadmap

- **Multi-Server Support**: Quick-switch functionality for diverse VPS locations (e.g., Estonia 🇪🇪, Netherlands 🇳🇱).
- **TUI Dashboard**: A terminal-based visual monitor for traffic and protocol health.
- **Automated MTU Probing**: Future enhancement to dynamically probe the best MTU per network session.

## 🌟 Support
If you find this tool useful, please **leave a star**! Every star is huge motivation for a 15-year-old dev on a mission to Europe. 🚀🦾
