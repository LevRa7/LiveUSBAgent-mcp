# AmneziaWG + subscription plan (2026-11-23)

## Goal

Add AmneziaWG (obfuscated WireGuard) config import and a subscription URL/QR
flow to the Kiosk network settings. This is the first stage; Happ-style
Xray/VLESS/VMess/Trojan subscriptions are a separate later stage.

## Config format

AmneziaWG config is an INI like WireGuard with extra `[Interface]` keys:

```
[Interface]
PrivateKey = ...
Address = 10.8.0.2/32
DNS = 1.1.1.1
Jc = 4
Jmin = 50
Jmax = 1000
S1 = 20
S2 = 20
H1 = 1234567
H2 = 2345678
H3 = 3456789
H4 = 4567890
[Peer]
PublicKey = ...
PresharedKey = ...
Endpoint = host:port
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
```

Validation: `[Interface]` and `[Peer]` required; `PrivateKey`, `Address`,
`PublicKey`, `Endpoint` required; `Jc/Jmin/Jmax/S1/S2/H1-H4` optional;
reject control characters and absurdly long lines. Never echo private or
preshared keys.

## Backend

- `GET /api/network/amnezia` -> configured files, `awg show` status (if
  available), `awg_available`.
- `POST /api/network/amnezia` body `{name, config}` -> write
  `/etc/amnezia/amneziawg/<safe-name>.conf` 0600, run
  `awg-quick up <path>`, return `{ok, name, output, redacted:true}`.
- `POST /api/network/amnezia/down` body `{name}` -> `awg-quick down <path>`.
- `POST /api/network/subscription` body `{url, name}` -> HTTPS-only fetch with
  timeout, 1 MB cap, DNS-rebinding/private-IP rejection; accept plain config or
  base64/base64url encoded config; save and bring up like above. Return
  `{ok, name, source, redacted:true}`.
- `GET /api/network/subscription/qr?url=...` -> SVG QR of the subscription URL
  (HTTPS only, length cap), for scanning into a phone.
- All POSTs behind the existing loopback/Host/Origin/CSRF/JSON gate; argv only,
  no shell; 400 validation, 503 missing awg/root, never raise.

## Frontend

Add a **«AmneziaWG»** tab in «Настройки сети»:
- name + config textarea + «Поднять AmneziaWG» + «Остановить»;
- subscription URL + «Загрузить подписку»;
- QR image of the subscription URL (fetched from the endpoint), copy button;
- show status/message, never render private/preshared keys, clear the textarea
  after successful submit.

## ISO

Build AmneziaWG in the chroot hook (no Debian package):
- clone `https://github.com/amnezia-vpn/amneziawg-tools.git`, build and install
  `awg` / `awg-quick`;
- clone the kernel module, DKMS add/build/install for the target kernel;
- warn and continue if either source build fails; add `libmnl-dev`,
  `libelf-dev`, `pkg-config` build deps.
- Add a `--check` assertion for the hook text and `awg-quick` install path.
- Secure Boot note: the module is unsigned.

## Non-goals now

Happ/Xray `vless://`, `vmess://`, `trojan://`, `ss://` subscriptions; camera QR
scanning. Those need Xray-core, a subscription parser, routing and camera UI.
