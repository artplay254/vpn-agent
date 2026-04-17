# vpn-agent

`vpn_cli.py` manages VPN connectivity with protocol fallback:
- WireGuard (`wg`)
- AmneziaWG (`awg`)
- XRay VLESS Reality (`vless`)

## Quick start
- **Run**: `sudo python vpn_cli.py connect`
- **Force VLESS**: `sudo python vpn_cli.py connect --proto vless`
- **Status**: `sudo python vpn_cli.py status`
- **Stop**: `sudo python vpn_cli.py disconnect`

## VLESS client config
- Copy `vless.json.example` to `vless.json` and fill in:
  - `YOUR_SERVER_IP_OR_DOMAIN`
  - `YOUR_UUID`
  - `YOUR_PUBLIC_KEY`
  - `YOUR_SHORT_ID`

## Server setup
Run `setup_server.sh` on your server to install:
- AmneziaWG components
- XRay Reality server config