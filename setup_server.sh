#!/usr/bin/env bash

set -euo pipefail

# --- Professional Server Setup for VPN-Agent ---
# Target OS: Ubuntu 24.04 LTS
# Description: Installs AmneziaWG and XRay (Reality) server configs.

# Colors for better UI
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== VPN-Agent Server Setup v0.2.0 ===${NC}"

# 1. Root Check
if [ "$EUID" -ne 0 ]; then 
  echo -e "${RED}❌ Error: Please run as root (use sudo).${NC}"
  exit 1
fi

# 2. System Update
echo -e "\n${BLUE}🔄 Updating system repositories...${NC}"
apt update && apt upgrade -y

# Basic tooling
apt install -y curl openssl

# 3. AmneziaWG Installation
if ! command -v awg &> /dev/null; then
    echo -e "${BLUE}🚀 Adding AmneziaWG PPA and installing tools...${NC}"
    apt install -y software-properties-common
    add-apt-repository -y ppa:amnezia/ppa
    apt update && apt install -y amneziawg iptables-persistent
else
    echo -e "${GREEN}✅ AmneziaWG is already installed.${NC}"
fi

# 4. Network Forwarding & Optimization
echo -e "${BLUE}⚙️  Optimizing network for VPN traffic...${NC}"

# Enable IPv4 Forwarding if not already enabled
if [[ $(sysctl -n net.ipv4.ip_forward) -eq 0 ]]; then
    sysctl -w net.ipv4.ip_forward=1
    echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
    echo -e "${GREEN}✅ IPv4 Forwarding enabled.${NC}"
else
    echo -e "${GREEN}✅ IPv4 Forwarding already active.${NC}"
fi

# 5. Directory Preparation
echo -e "${BLUE}📂 Preparing configuration directories...${NC}"
mkdir -p /etc/amnezia/amneziawg/
chmod 700 /etc/amnezia/amneziawg/

# 6. XRay Reality (VLESS) installation + server config
echo -e "\n${BLUE}🛰️  Installing XRay...${NC}"
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install

echo -e "\n${BLUE}🔑 Generating XRay keys...${NC}"
KEYS=$(xray uuid && xray x25519)
UUID=$(echo "$KEYS" | head -n 1)
PRIV_KEY=$(echo "$KEYS" | sed -n '2p' | cut -d' ' -f3)
PUB_KEY=$(echo "$KEYS" | sed -n '3p' | cut -d' ' -f3)
SHORT_ID=$(openssl rand -hex 8)

echo -e "\n${BLUE}🧾 Writing XRay server config...${NC}"
install -d -m 755 /usr/local/etc/xray
cat <<EOF > /usr/local/etc/xray/config.json
{
  "log": { "loglevel": "warning" },
  "inbounds": [
    {
      "port": 443,
      "protocol": "vless",
      "settings": {
        "clients": [{ "id": "$UUID", "flow": "xtls-rprx-vision" }],
        "decryption": "none"
      },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "show": false,
          "dest": "www.microsoft.com:443",
          "xver": 0,
          "serverNames": ["www.microsoft.com"],
          "privateKey": "$PRIV_KEY",
          "shortIds": ["$SHORT_ID"]
        }
      }
    }
  ],
  "outbounds": [{ "protocol": "freedom" }]
}
EOF

echo -e "\n${BLUE}🔁 Enabling XRay service...${NC}"
systemctl restart xray
systemctl enable xray

# 7. Final Instructions
echo -e "\n${GREEN}==============================================${NC}"
echo -e "${GREEN}✅ SERVER COMPONENTS INSTALLED SUCCESSFULLY!${NC}"
echo -e "${GREEN}==============================================${NC}"
echo -e "\n${BLUE}Next Steps for the Saiyan Mindset:${NC}"
echo -e "1. Create your server config: ${NC}nano /etc/amnezia/amneziawg/awg0.conf"
echo -e "2. Use the following template logic (replace with your keys):"
echo -e "   --------------------------------------------------"
echo -e "   [Interface]"
echo -e "   Address = 10.0.1.1/24"
echo -e "   ListenPort = 443"
echo -e "   PrivateKey = <SERVER_PRIVATE_KEY>"
echo -e "   PostUp = iptables -A FORWARD -i awg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o ens1 -j MASQUERADE"
echo -e "   PostDown = iptables -D FORWARD -i awg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o ens1 -j MASQUERADE"
echo -e "   --------------------------------------------------"
echo -e "3. Start the server: ${NC}sudo awg-quick up awg0"
echo -e "\n${BLUE}Check the README.md in your local repo for client setup.${NC}"

echo -e "\n${GREEN}--- SERVER SETUP COMPLETE ---${NC}"
echo -e "UUID: ${UUID}"
echo -e "Public Key: ${PUB_KEY}"
echo -e "Short ID: ${SHORT_ID}"
echo -e "${GREEN}--- COPY THESE TO YOUR vless.json ---${NC}"