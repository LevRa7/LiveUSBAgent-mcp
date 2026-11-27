# Network settings + domain correction (2026-11-23)

## Domain

the old domain is not a working domain. Every active code/doc/test
reference has been replaced with:

`gateway.example.ts.net`

`core/domain.py::DEFAULT_PUBLIC_BASE_URL`, `build-iso.sh --gateway`, README,
docs, tests and release notes now use this host.

## Network settings in the Kiosk

The network screen gets a **«Настройки сети»** dialog with:

1. **Current identifiers and addresses (read-only).**
   - hostname, node id, link key (masked, show toggle);
   - each interface: name, MAC, state, connection name, IPv4/CIDR, gateway,
     DNS list;
   - default route and `/etc/resolv.conf` DNS.

2. **IPv4 / DNS editor.**
   - select interface;
   - mode DHCP (`auto`) or static (`manual`);
   - static address/prefix, gateway, DNS (comma-separated).
   - Apply through NetworkManager (`nmcli con mod` + `nmcli con up`), no shell.

3. **PPPoE.**
   - username, password, optional service;
   - create/replace a `rhm-pppoe` NetworkManager connection, bring it up.

4. **WireGuard.**
   - name, private key, address/prefix, peer public key, endpoint, allowed IPs,
     persistent keepalive;
   - write a `0600` NetworkManager keyfile and `nmcli con reload` + up.

5. **OpenVPN.**
   - paste a `.ovpn` config; import with
     `nmcli connection import type openvpn file <temp>`, then up.
   - private keys/passwords are never logged; temp file mode 0600.

### API contract

- `GET /api/network` ->
  `{"ok": true, "hostname", "node_id", "link_key", "interfaces": [...], "dns": [...], "routes": [...]}`.
- `POST /api/network/ip` body
  `{"interface", "mode":"auto"|"manual", "address", "prefix", "gateway", "dns"}`.
- `POST /api/network/pppoe` body
  `{"username", "password", "service"}`.
- `POST /api/network/wireguard` body
  `{"name", "private_key", "address", "prefix", "peer_public_key", "endpoint", "allowed_ips", "keepalive"}`.
- `POST /api/network/openvpn` body `{"name", "config"}`.
- All POSTs sit behind the existing loopback/Host/Origin/CSRF/JSON guard.
- 200 success, 400 validation, 503 when NetworkManager/root/tool is unavailable.
- Responses never echo passwords/private keys (report `redacted: true` instead).

### UI

- `Настройки сети` button on the network view.
- One modal with tabs/sections: «Текущие», «IP и DNS», «PPPoE», «WireGuard»,
  «OpenVPN»; current identifiers visible, token with eye toggle.
- Validation feedback and success/error messages in the modal.
- Keep the existing Wi-Fi, apps, terminal and QR flows intact.

### ISO packages

Add `ppp`, `wireguard-tools`, `openvpn`, `network-manager-openvpn` and
`network-manager-openvpn-gnome` (or `network-manager-openvpn`) to the Kiosk
package list; add them to `--check`.
