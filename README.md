# 🛡️ VPN-Agent

**A resilient, auto-switching VPN CLI for Linux that beats DPI blocking.**

VPN-Agent is a lightweight Python utility designed for users in restricted network environments. It manages **Standard WireGuard** and **AmneziaWG** (obfuscated) connections, implementing an intelligent "Saiyan-mindset" logic: if the standard protocol is blocked by Deep Packet Inspection (DPI), it automatically fails over to an obfuscated tunnel.

## 🚀 Features

  * **Intelligent Fallback**: Attempts standard WireGuard connections first and switches to AmneziaWG only when necessary.
  * **Auto-Recovery (Daemon)**: Periodically checks if the network restriction has been lifted to revert back to the high-performance standard WireGuard.
  * **Latency Tracking**: Real-time RTT (Round Trip Time) monitoring integrated directly into the status dashboard.
  * **Developer-Friendly**: Built with a clean CLI, modular configuration, and root-safety checks.

-----

## 🛠️ Installation

### 1\. Server Setup (Ubuntu 24.04)

Run the automated setup script on your VPS to install AmneziaWG and optimize network forwarding:

```bash
wget https://raw.githubusercontent.com/artplay254/vpn-agent/main/setup_server.sh
chmod +x setup_server.sh
sudo ./setup_server.sh
```

### 2\. Client Setup (Arch Linux/Ubuntu)

Clone the repository into your local configuration directory:

```bash
mkdir -p ~/.config/vpn-agent
git clone https://github.com/artplay254/vpn-agent.git ~/.config/vpn-agent
cd ~/.config/vpn-agent
```

### 3\. Configuration

Copy the provided templates and add your specific keys/endpoints:

```bash
cp client_wg.conf.example client_wg.conf
cp client_awg.conf.example client_awg.conf
# Edit files with your favorite editor (e.g., nano or nvim)
```

-----

## 📖 Usage

Create an alias in your `.zshrc` or `.bashrc` for quick access:
`alias vpn='sudo python3 ~/.config/vpn-agent/vpn_cli.py'`.

| Command | Description |
| :--- | :--- |
| `vpn up` | Connect using the best available protocol (Auto-fallback). |
| `vpn status` | Show active protocol, real-time latency, and transfer stats. |
| `vpn down` | Gracefully disconnect and clean up network interfaces. |
| `vpn daemon` | Start the background monitor for auto-recovery. |
| `vpn --version` | Check the current version of the agent. |

-----

## 🏗️ Tech Stack

  * **Language**: Python 3.12+
  * **Environment**: Arch Linux (KDE Plasma)
  * **Protocols**: WireGuard & AmneziaWG
  * **Networking**: Linux `iproute2`, `iptables`, and `ping` utilities

## 📝 License

Distributed under the MIT License. See `LICENSE` for more information.

-----

**Author**: [Artem Semenihin](https://www.google.com/search?q=https://github.com/artplay254) — *Aspiring Full-Stack Developer*
