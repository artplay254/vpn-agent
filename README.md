# 🛡️ VPN-Agent (v0.3.0) — The "Evolution" Update

**A self-learning, multi-protocol VPN manager designed to stay invisible and resilient against DPI.**

VPN-Agent is an adaptive CLI tool for Linux that doesn't just manage tunnels — it learns from its failures. Using a scoring engine and mutation logic, it automatically navigates through blocks to ensure you never lose internet access.

---

## 🧠 How It Works: The "Learning" Loop

Unlike standard VPN managers, VPN-Agent operates on a **Feedback Loop**. It treats every connection as a data point.

### 🔄 The Architecture Flow:
1. **Network Identification**: Upon start, the Agent detects your current **ISP/SSID** (Network Fingerprinting).
2. **Scoring Engine (Brain)**: It queries the `Brain` module to find the highest-rated protocol/config for *this specific network* based on historical success and latency.
3. **Execution**:
    - **Success**: Great! The Agent logs the latency and maintains the tunnel.
    - **Fail**: The Agent marks the config as "failed" and immediately tries the next protocol in the hierarchy.
4. **Mutation (Evolution)**: If all standard configs fail, the `ConfigMutator` generates a new "Variant" (mutating MTU, Ports, or Obfuscation headers) to find a way through the firewall.

### 🗺️ System Flowchart
```mermaid
graph TD
    A[User: vpn connect] --> B{Brain: Get Best Score}
    B -->|High Score| C[Try Best Config]
    B -->|No History| D[Standard Fallback]
    C -->|Success| E[Update Brain: +Score]
    C -->|Fail| F[Update Brain: -Score]
    F --> G[Try Next Protocol]
    G -->|All Fail| H[Genetic Mutation Mode]
    H --> I[Generate New Variant]
    I --> C
```

---

## ✨ Features

- **ISP-Aware Logic**: Remembers that *VLESS Reality* works best at School, while *AmneziaWG* is faster at Home.
- **Genetic Optimization**: Automatically tunes sensitive parameters like **MTU**, **Junk Packets (Jc/Jmin/Jmax)**, and **Ports**.
- **Multi-Layer Defense**:
    - **Layer 1: WireGuard**: Standard high-speed performance.
    - **Layer 2: AmneziaWG**: Handshake obfuscation to beat standard DPI.
    - **Layer 3: VLESS + Reality**: Stealth TLS masking to bypass strict state firewalls.
- **Self-Healing Daemon**: Background monitor that recovers connections and optimizes routing in real-time.

---

## 🚀 Detailed Installation & Setup

### 1. Server-Side Deployment (Ubuntu 24.04 recommended)
Deploy your own multi-protocol stack with a single command:
```bash
wget [https://raw.githubusercontent.com/artplay254/vpn-agent/main/setup_server.sh](https://raw.githubusercontent.com/artplay254/vpn-agent/main/setup_server.sh)
chmod +x setup_server.sh
sudo ./setup_server.sh
```
*The script will output your WireGuard, AmneziaWG, and VLESS configurations. **Save them.***

### 2. Client-Side Installation (Arch/Linux)
```bash
# Clone the project
git clone [https://github.com/artplay254/vpn-agent](https://github.com/artplay254/vpn-agent) ~/.config/vpn-agent
cd ~/.config/vpn-agent

# Create necessary directories
mkdir variants
```

### 3. Configuration Setup
Place your server configs in `~/.config/vpn-agent/`:
- **WireGuard**: `client_wg.conf`
- **AmneziaWG**: `client_awg.conf`
- **VLESS**: `vless.json` (Use `vless.json.example` as a template)

**Important (Arch Users):** Give Xray permission to manage network interfaces:
```bash
sudo setcap "cap_net_admin,cap_net_bind_service+ep" $(which xray)
```

---

## 🛠 Usage & Commands

Add an alias to your `~/.zshrc` or `~/.bashrc`:
`alias vpn='sudo python3 ~/.config/vpn-agent/vpn_cli.py'`

| Command | Description |
| :--- | :--- |
| `vpn connect` | Intelligent connection based on Brain scores. |
| `vpn disconnect` | Cleanly shuts down tunnels and restores routing. |
| `vpn status` | Real-time report: Active protocol, ISP, and Traffic flow status. |
| `vpn daemon` | Starts background monitoring & auto-recovery. |

**Advanced:** Force a specific protocol:
`vpn connect --protocol vless`

---

## 🔜 Roadmap
- [ ] **Auto-Cleanup**: A "Natural Selection" worker to prune low-scoring variants.
- [ ] **Web Dashboard**: A local FastAPI UI to visualize connection success over time.
- [ ] **Geo-Awareness**: Optimized protocol selection based on GPS/GeoIP.

## 🌟 Support
Built by a 15-year-old dev with a mission to learn and build tools that matter. If this tool helped you stay connected, please **leave a star**! 🚀🦾
