# 🛡️ VPN-Agent

A lightweight Python CLI for Linux (Arch/Ubuntu) that manages WireGuard and AmneziaWG connections with intelligent fallback and auto-recovery logic. Built to bypass DPI blocks while maintaining high performance.

## ✨ Features
- **Smart Fallback**: Automatically tries standard WireGuard (3 attempts) before falling back to AmneziaWG obfuscation.
- **Latency Monitoring**: Real-time RTT (ms) tracking visible in the status command.
- **Auto-Recovery (Daemon)**: Monitors connection health and automatically attempts to revert to standard WireGuard if the network block is lifted.
- **English-First CLI**: Professional command-line interface designed for developers.

## 🚀 Installation

### 1. Server Side (Ubuntu 24.04)
Run the automated setup script to install dependencies and enable IP forwarding:
```bash
wget [https://raw.githubusercontent.com/artplay254/vpn-agent/main/setup_server.sh](https://raw.githubusercontent.com/artplay254/vpn-agent/main/setup_server.sh)
chmod +x setup_server.sh
sudo ./setup_server.sh

```
### 2. Client Side (Arch/Linux)
Clone the repository to your local config folder:
```bash
git clone [https://github.com/artplay254/vpn-agent](https://github.com/artplay254/vpn-agent) ~/.config/vpn-agent
cd ~/.config/vpn-agent

```
Add your client_wg.conf and client_awg.conf to this directory.
### 3. Quick Alias
Add this to your ~/.zshrc or ~/.bashrc:
```bash
alias vpn='sudo python3 ~/.config/vpn-agent/vpn_cli.py'

```
## 🛠 Usage
- `vpn connect` — Connect with auto-protocol selection.
- `vpn connect --protocol wg|awg|vless` — Force a specific protocol.
- `vpn status` — Show active tunnel info (interface, traffic, public IP).
- `vpn daemon` — Run in background for health monitoring and auto-recovery.
- `vpn disconnect` — Disconnect all tunnels.

Deprecated aliases (still supported): `vpn up`, `vpn down`, `--proto`.
## 🔜 Roadmap
The project is under active development. Upcoming features include:

- **Expanded Protocol Suite**: Support for additional obfuscation protocols (Xray/VLESS, ShadowTLS) to stay ahead of DPI.
- **Auto-Optimization Engine**: Intelligent system that automatically tunes **MTU** and **Port** selection based on network conditions and packet loss.
- **Log-Driven Intelligence**: A data-driven approach that analyzes connection logs to identify blocking patterns and adapt in real-time.
- **Multi-Server Support**: Quick-switch functionality for geographically diverse VPS locations.
- **TUI Dashboard**: A terminal-based interface for visual monitoring of traffic and protocol health.

## 🌟 Support
If you find this tool useful, please **leave a star** to help the project grow! Every star is a huge motivation for a 9th-grade dev on a mission to Europe. 🚀