# 🛡️ VPN-Agent

**An adaptive, self-learning VPN orchestrator designed to maintain persistent connectivity in high-censorship environments through protocol evolution.**

VPN-Agent is an intelligent CLI manager that treats internet censorship as a dynamic problem. Unlike static VPN clients, it utilizes a **Genetic Mutation Engine**, **Binary Search Probing**, and **SQLite Analytics** to navigate through Deep Packet Inspection (DPI) and protocol-based blocking.

---

## 🏗️ System Architecture & Logic

VPN-Agent acts as a high-level orchestrator sitting above low-level protocol binaries. It manages the lifecycle of connections by interfacing with the Linux network stack and a persistent data "Brain."



### 1. The Controller (`vpn_cli.py`)
The entry point. It manages user commands, validates the environment, and coordinates between the Database and the Mutator.

### 2. The Brain (`database.py`)
A persistent **SQLite** backend that tracks every connection attempt.
* **Network Fingerprinting**: Identifies networks by SSID or ISP gateway.
* **Scoring Algorithm**: Ranks configurations using a weighted formula:
  **Reliability Score** = $(Success Rate \times 0.7) + (Latency Factor \times 0.3)$.

### 3. The Recon Engine (`prober.py`)
Before connecting to a new network, the Agent performs **Binary Search MTU Probing**. It sends a series of ICMP/UDP packets with the `DF` (Don't Fragment) bit set to find the exact physical MTU limit of the current path, preventing handshake failures before they happen.

### 4. The Evolutionary Logic (`config.py`)
When standard protocols are blocked, the **Guided Mutation** engine triggers. It analyzes past failures and shifts parameters (MTU, Junk packet size, obfuscation headers) in the direction of most recent success, creating "variants" that evolve to bypass the firewall.

---

## 🛠 Installation Guide

### 1. Server-Side Setup
Deploy the multi-protocol stack (WireGuard, AmneziaWG, and Xray-core) on a clean **Ubuntu 24.04** VPS:

```bash
wget https://raw.githubusercontent.com/artplay254/vpn-agent/main/setup_server.sh
chmod +x setup_server.sh
sudo ./setup_server.sh
```
*Save the generated config files (WG, AWG, and VLESS) for the client setup.*

### 2. Client-Side Dependencies
The Agent requires the underlying protocol binaries to be installed on your local machine.

**For Arch Linux:**
```bash
# Core protocols and tools
sudo pacman -S wireguard-tools xray iproute2 sqlite

# AmneziaWG (requires DKMS or pre-built kernel module)
# Use an AUR helper like yay or paru
yay -S amneziawg-tools-git amneziawg-dkms-git
```

**Python Environment:**
```bash
pip install rich
```

### 3. Client Installation
```bash
git clone https://github.com/artplay254/vpn-agent ~/.config/vpn-agent
cd ~/.config/vpn-agent
mkdir variants logs
```

### 4. Configuration
Place your server-generated files into `~/.config/vpn-agent/`:
* `client_wg.conf`
* `client_awg.conf`
* `vless.json`

**Set Capabilities:**
Allow Xray to manage network interfaces without needing full `sudo` for every packet operation:
```bash
sudo setcap "cap_net_admin,cap_net_bind_service+ep" $(which xray)
```

---

## ⌨️ Command Reference

| Command | Action | Technical Detail |
| :--- | :--- | :--- |
| `vpn connect` | **Intelligent Connect** | Probes MTU $\rightarrow$ Queries Brain $\rightarrow$ Executes Protocol. |
| `vpn disconnect` | **Clean Exit** | Tears down TUN/TAP, flushes routes, and kills PIDs. |
| `vpn stats` | **Analytics** | Displays SQLite data: Best configs per network & ISP. |
| `vpn status` | **Live Monitor** | Real-time traffic flow and handshake validation. |
| `vpn daemon` | **Auto-Pilot** | Background monitoring with auto-mutation on block. |

---

## 🧬 Advanced Features

* **WAL-Mode Persistence**: SQLite uses Write-Ahead Logging for high-performance concurrent metric tracking.
* **Atomic Config Management**: Variants are written using temporary files to prevent corruption during system crashes.
* **SIGTERM/SIGKILL Lifecycle**: Clean process management ensures no "zombie" Xray or WireGuard processes remain after disconnection.
* **TCP Handshake Validation**: The Agent verifies internet health by attempting a real TLS handshake to `1.1.1.1:443`, ensuring the VPN isn't just "up" but actually "routing."

---

## 🌟 Support

Built for those who refuse to be limited by digital borders. If this tool keeps you connected, consider leaving a **star** on GitHub! 🚀🦾
