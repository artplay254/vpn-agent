#!/usr/bin/env bash

set -euo pipefail

# --- VPN-Agent Server Setup v0.4.0 ---
# Target OS: Ubuntu 24.04 LTS
# Full Stack: WireGuard, AmneziaWG, XRay Reality

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=== VPN-Agent Server Setup v0.4.0 [Saiyan Edition] ===${NC}"

# 1. Root Check
[[ "$EUID" -ne 0 ]] && echo -e "${RED}❌ Run as root!${NC}" && exit 1

# 2. System Prep & Interface Detection
echo -e "\n${BLUE}🔍 Detecting network environment...${NC}"
ETH_IFACE=$(ip route | grep default | awk '{print $5}' | head -n1)
PUBLIC_IP=$(curl -s https://ifconfig.me)
echo -e "Interface: ${GREEN}$ETH_IFACE${NC}, IP: ${GREEN}$PUBLIC_IP${NC}"

# 3. Installation
echo -e "\n${BLUE}🚀 Installing Full Stack (WG, AWG, XRay)...${NC}"
apt update && apt upgrade -y
apt install -y software-properties-common curl openssl wireguard wireguard-tools iptables-persistent

if ! command -v awg &> /dev/null; then
    add-apt-repository -y ppa:amnezia/ppa
    apt update && apt install -y amneziawg
fi

bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install

# 4. Network Optimization
echo -e "\n${BLUE}⚙️  Configuring Sysctl & Forwarding...${NC}"
echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-vpn-agent.conf
sysctl --system

# 5. WireGuard Setup (Standard)
echo -e "\n${BLUE}🔒 Setting up Standard WireGuard (Port 51820)...${NC}"
WG_PRIV=$(wg genkey)
WG_PUB=$(echo "$WG_PRIV" | wg pubkey)
CLIENT_WG_PRIV=$(wg genkey)
CLIENT_WG_PUB=$(echo "$CLIENT_WG_PRIV" | wg pubkey)

cat <<EOF > /etc/wireguard/wg0.conf
[Interface]
Address = 10.0.1.1/24
ListenPort = 51820
PrivateKey = $WG_PRIV
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o $ETH_IFACE -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o $ETH_IFACE -j MASQUERADE

[Peer]
PublicKey = $CLIENT_WG_PUB
AllowedIPs = 10.0.1.2/32
EOF

systemctl enable --now wg-quick@wg0

# 6. AmneziaWG Setup (Obfuscated)
echo -e "\n${BLUE}🛡️  Setting up AmneziaWG (Port 51821)...${NC}"
AWG_PRIV=$(awg genkey)
AWG_PUB=$(echo "$AWG_PRIV" | awg pubkey)
CLIENT_AWG_PRIV=$(awg genkey)
CLIENT_AWG_PUB=$(echo "$CLIENT_AWG_PRIV" | awg pubkey)

mkdir -p /etc/amnezia/amneziawg/
cat <<EOF > /etc/amnezia/amneziawg/awg0.conf
[Interface]
Address = 10.0.2.1/24
ListenPort = 51821
PrivateKey = $AWG_PRIV
PostUp = iptables -A FORWARD -i awg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o $ETH_IFACE -j MASQUERADE
PostDown = iptables -D FORWARD -i awg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o $ETH_IFACE -j MASQUERADE
# Default obfuscation (Disabled by default for stability)
Jc = 0; Jmin = 0; Jmax = 0; S1 = 0; S2 = 0;

[Peer]
PublicKey = $CLIENT_AWG_PUB
AllowedIPs = 10.0.2.2/32
EOF

systemctl enable --now awg-quick@awg0

# 7. XRay Reality (Stealth)
echo -e "\n${BLUE}🛰️  Configuring XRay Reality (Port 443)...${NC}"
UUID=$(xray uuid)
X_KEYS=$(xray x25519)
PRIV_KEY=$(echo "$X_KEYS" | grep "Private key:" | awk '{print $3}')
PUB_KEY=$(echo "$X_KEYS" | grep "Public key:" | awk '{print $3}')
SHORT_ID=$(openssl rand -hex 8)

cat <<EOF > /usr/local/etc/xray/config.json
{
  "log": { "loglevel": "warning" },
  "inbounds": [{
    "port": 443, "protocol": "vless",
    "settings": { "clients": [{"id": "$UUID", "flow": "xtls-rprx-vision"}], "decryption": "none" },
    "streamSettings": {
      "network": "tcp", "security": "reality",
      "realitySettings": {
        "show": false, "dest": "www.microsoft.com:443", "xver": 0,
        "serverNames": ["www.microsoft.com"], "privateKey": "$PRIV_KEY",
        "shortIds": ["$SHORT_ID"]
      }
    }
  }],
  "outbounds": [{"protocol": "freedom"}]
}
EOF
systemctl restart xray

# 8. Final Output
echo -e "\n${GREEN}==================================================${NC}"
echo -e "${GREEN}✅ SERVER IS READY FOR v0.4.0 BRAIN${NC}"
echo -e "${GREEN}==================================================${NC}"
echo -e "Public IP: ${BLUE}$PUBLIC_IP${NC}"
echo -e "\n${BLUE}--- Standard WireGuard (wg0) ---${NC}"
echo -e "Server PubKey: $WG_PUB"
echo -e "Client PrivKey: $CLIENT_WG_PRIV"
echo -e "Client IP: 10.0.1.2"

echo -e "\n${BLUE}--- AmneziaWG (awg0) ---${NC}"
echo -e "Server PubKey: $AWG_PUB"
echo -e "Client PrivKey: $CLIENT_AWG_PRIV"
echo -e "Client IP: 10.0.2.2"

echo -e "\n${BLUE}--- XRay Reality (VLESS) ---${NC}"
echo -e "UUID: $UUID"
echo -e "Public Key: $PUB_KEY"
echo -e "Short ID: $SHORT_ID"
echo -e "${GREEN}==================================================${NC}"