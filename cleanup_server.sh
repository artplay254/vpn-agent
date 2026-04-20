#!/usr/bin/env bash

set -euo pipefail

# --- VPN-Agent Server Cleanup v0.4.0 ---
# Target OS: Ubuntu 24.04 LTS
# Description: Completely removes WG, AWG, and XRay settings.

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${RED}=== VPN-Agent Server Cleanup [Deep Clean] ===${NC}"

# 1. Root Check
[[ "$EUID" -ne 0 ]] && echo -e "${RED}❌ Run as root!${NC}" && exit 1

read -p "⚠️  This will DELETE all VPN configs and stop all services. Are you sure? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    exit 1
fi

# 2. Stop and Disable Services
echo -e "\n${BLUE}🛑 Stopping services...${NC}"
systemctl stop wg-quick@wg0 || true
systemctl disable wg-quick@wg0 || true
systemctl stop awg-quick@awg0 || true
systemctl disable awg-quick@awg0 || true
systemctl stop xray || true
systemctl disable xray || true

# 3. Clean IPTables (NAT rules)
echo -e "${BLUE}🛡️  Cleaning IPTables rules...${NC}"
# Находим интерфейс
ETH_IFACE=$(ip route | grep default | awk '{print $5}' | head -n1)

# Удаляем правила маскарадинга, если они есть
iptables -t nat -D POSTROUTING -o "$ETH_IFACE" -j MASQUERADE || true
iptables -D FORWARD -i wg0 -j ACCEPT || true
iptables -D FORWARD -i awg0 -j ACCEPT || true

# Сохраняем пустые правила
if command -v iptables-save &> /dev/null; then
    netfilter-persistent save || true
fi

# 4. Remove Configuration Files
echo -e "${BLUE}📂 Deleting configuration directories...${NC}"
rm -rf /etc/wireguard/wg0.conf
rm -rf /etc/amnezia/amneziawg/
rm -rf /usr/local/etc/xray/
rm -rf /etc/sysctl.d/99-vpn-agent.conf
sysctl --system

# 5. Uninstall Software (Optional but recommended for deep clean)
echo -e "${BLUE}🗑️  Uninstalling packages...${NC}"
apt purge -y wireguard wireguard-tools amneziawg xray || true
apt autoremove -y

# 6. Remove Amnezia PPA
if [ -f /etc/apt/sources.list.d/amnezia-ubuntu-ppa-*.list ]; then
    add-apt-repository --remove -y ppa:amnezia/ppa
fi

echo -e "\n${GREEN}==================================================${NC}"
echo -e "${GREEN}✨ SERVER IS CLEAN! Everything has been removed.${NC}"
echo -e "${GREEN}==================================================${NC}"