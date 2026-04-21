# 🛡️ VPN-Agent

**A smart, self-learning VPN manager that automatically dodges network blocks to keep you connected.**

Current release: **v0.4.5**

Standard VPN clients are dumb: if a network blocks their protocol, they just fail. **VPN-Agent** is different. It acts like a digital lockpicker. If a firewall blocks your connection, the Agent analyzes the block, tweaks its settings, and tries again until it breaks through.

---

## 👁️ Visualizing the Logic

Here is exactly how the Agent thinks when you type `vpn connect`:

```mermaid
graph TD
    A[Start: vpn connect] --> B{Have I seen this WiFi/Network before?}

    B -- No --> C[🔍 Probe Network Limits MTU]
    C --> D[Save to Database]

    B -- Yes --> E[🧠 Ask Database: What is the best config here?]
    D --> E

    E --> F{Exploit or Explore?}
    F --> G[🚀 Try to Connect]
    G --> H{Traffic works?}

    H -- Yes --> I[🛡️ Watchdog re-validates for 300s]
    I --> J{Stable?}
    J -- Yes --> K[✅ Keep session and score it]
    J -- No --> L[🧠 Mark stale + re-probe MTU]
    L --> M[🔁 Switch protocol immediately]

    H -- Blocked! --> N[🧬 Guided Mutation Engine]
    N --> O[Change MTU, Ports, or Obfuscation]
    O --> G
```

---

## 🧠 Deep Dive: Under the Hood

VPN-Agent isn't just a wrapper; it’s a decision-making engine. It operates using three core subsystems that communicate via a local **SQLite** state machine.

### 1. The Decision Engine (The Brain)

The Agent treats every network (identified by SSID or Gateway IP) as a unique environment. It stores metrics in `agent_brain.db` using **WAL (Write-Ahead Logging)** for high-concurrency performance.

**The Reliability Formula:**
To choose the best configuration, the Agent calculates a weighted score for every available variant:
$$Score = (SuccessRate \times 0.7 + LatencyFactor \times 0.3) \times e^{-\lambda t}$$

- **Success Rate**: The ratio of successful handshakes to total attempts.
- **Latency Factor**: A normalized value where lower pings result in a higher score.
- **Recency Weight**: Metrics stay at full strength for the first **24 hours**, then decay automatically so fresh observations beat old "perfect" history.
- **Stale Filtering**: Any session that fails early re-validation is marked `stale` in SQLite and excluded from future ranking.

**Selection Policy:**

- **90% of the time**, the Agent exploits the highest-ranked protocol/config for the current network.
- **10% of the time**, it explores a lower-ranked option to test whether blocked paths have recovered.
- The CLI logs these events with **Rich**, so you'll see messages like exploration mode or brain decay being applied.

### 2. The Reconnaissance Module (Binary MTU Prober)

Standard VPNs often fail because of **MTU (Maximum Transmission Unit)** mismatch, leading to packet fragmentation that DPI firewalls easily drop.

The Agent solves this by implementing a **Binary Search Prober**:

1.  **The Range**: It targets a window between **1200** and **1500** bytes.
2.  **The Probe**: It sends ICMP/UDP packets with the `DF` (Don't Fragment) bit enabled.
    - Command: `ping -M do -s <payload_size>`
    - Note: `Payload = MTU - 28` (20 bytes for IP header + 8 bytes for ICMP header).
3.  **The Logic**: It halves the search space with every packet. If a packet of 1400 fails, it tries 1300. If 1300 passes, it tries 1350.
4.  **The Goal**: Finding the absolute maximum packet size in $\approx 8$ attempts.

### 3. Guided Mutation (The Evolutionary Loop)

When standard configs are blocked, the `ConfigMutator` generates "Variants." This isn't random; it's a **Directed Gradient Search**:

- **Parameter Tracking**: If a mutation that decreased MTU from 1400 to 1380 resulted in a successful (even if slow) connection, the Agent flags the "downward MTU trend" as a successful gene.
- **Exploration vs. Exploitation**: In v0.4.5, exploration happens first at the protocol/config selection layer. Mutation is the next fallback when that chosen path still fails.
- **Mutation Vectors**: It modifies MTU for WireGuard paths, `Jc`/`Jmin`/`Jmax` for AmneziaWG, and MTU/port choices for VLESS Reality.

### 4. Persistence & Process Lifecycle

The Agent ensures system stability through low-level process management:

- **Atomic State**: `variant_index.json` and config updates are written to temporary files first, then moved to the final destination to prevent corruption during power loss.
- **Hardened Shutdown**: The Agent uses a **SIGTERM $\rightarrow$ Wait (5s) $\rightarrow$ SIGKILL** sequence for the Xray and WireGuard binaries to ensure no "zombie" interfaces are left hanging.
- **Session Re-Validation**: A connection is not trusted immediately. The Agent opens a **300-second watchdog window** and repeatedly checks a real **TCP handshake** to `1.1.1.1:443`.
- **Early-Failure Response**: If the handshake fails during those first 5 minutes, the current success record is instantly marked **stale**, the Agent runs a fresh **binary-search MTU probe**, and it switches protocols immediately.
- **Rich Event Logging**: Important Brain events such as exploration mode, decay application, watchdog activation, and stale-session invalidation are shown directly in the CLI.

---

## 🛠 Installation Guide

### 1. Install the "Engines" (Client-side)

The Agent is the driver, but you still need the engine. Install the core VPN binaries:

**On Arch Linux:**

```bash
# Install WireGuard and XRay (for VLESS)
sudo pacman -S wireguard-tools xray iproute2

# Install AmneziaWG (requires an AUR helper like yay)
yay -S amneziawg-tools-git amneziawg-dkms-git
```

**On Ubuntu/Debian:**

```bash
# Install WireGuard
sudo apt update && sudo apt install wireguard-tools iproute2

# Install XRay (using official script)
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install

# For AmneziaWG, you may need to build from source or use third-party PPAs.
```

### 2. Server Setup

Run this on your **Ubuntu 24.04** VPS to set up the multi-protocol backend:

```bash
wget https://raw.githubusercontent.com/artplay254/vpn-agent/main/setup_server.sh
chmod +x setup_server.sh
sudo ./setup_server.sh
```

_Save the `client_wg.conf`, `client_awg.conf`, and `vless.json` files provided at the end._

### 3. Install the Agent

```bash
git clone https://github.com/artplay254/vpn-agent ~/.config/vpn-agent
cd ~/.config/vpn-agent
pip install rich  # For the terminal UI
mkdir variants logs
```

### 4. Final Permissions

Place your config files in `~/.config/vpn-agent/`. Then, give Xray permission to touch the network stack so you don't need `sudo` for every packet:

```bash
sudo setcap "cap_net_admin,cap_net_bind_service+ep" $(which xray)
```

---

## ⌨️ Command List

| Command          | What it does                                                                                                                                                  |
| :--------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `vpn connect`    | **The "Smart" Button.** Scores configs with freshness decay, may explore with 10% probability, connects, then watchdog-validates the session for 300 seconds. |
| `vpn disconnect` | Safely kills the tunnel and restores your original internet.                                                                                                  |
| `vpn stats`      | Shows which networks you've been on and what currently scores best there.                                                                                     |
| `vpn status`     | Real-time traffic, protocol health, and ISP info.                                                                                                             |
| `vpn daemon`     | Background mode: auto-reconnects on drop and periodically tries to recover back to the primary protocol.                                                      |

---

## 🌟 Support

Built by a 15-year-old developer with a **Saiyan Mindset**—constantly breaking limits to ensure digital freedom.

**If this tool keeps you connected, leave a ⭐ on GitHub!** 🚀🦾
