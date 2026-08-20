# claude-vpn-bypass

Keeps **Claude Code working while your corporate VPN is on**.

Some corporate VPNs (e.g. Cisco AnyConnect) route `api.anthropic.com` through an
SSL-inspection proxy and re-pin that route every few seconds. Claude Code then
dies with `Self-signed certificate detected`, and route-table fixes lose the
race — so you have to disconnect the VPN to use Claude Code.

This installs a tiny local proxy that sends **only** Anthropic traffic out your
normal internet connection (pinned to the physical interface via `IP_BOUND_IF`,
below the routing table the VPN keeps rewriting). TLS stays end-to-end to the
real Anthropic — no interception, no trusting any corporate CA. Everything else
keeps its normal path through the tunnel.

## Install

```sh
git clone https://github.com/alexandr-mironov/claude-vpn-bypass.git
cd claude-vpn-bypass
./install.sh
```

Then open a **new terminal** and use Claude Code as usual:

```sh
claude
```

That's it. Leave the VPN on. No `sudo`, nothing to launch each time — the proxy
starts at login and restarts itself if it dies.

## Uninstall

```sh
./uninstall.sh
```

## Health check

```sh
curl -x http://127.0.0.1:8888 -s https://api.anthropic.com/v1/messages \
  -o /dev/null -w '%{http_code}\n'
```

`405`/`401` = working. A connection error or `self-signed` means the service is
down — restart it:

```sh
launchctl kickstart -k gui/$(id -u)/com.anthropic-bypass
```

Live log (shows `[BYPASS en0]` / `[direct]` decisions):

```sh
tail -f ~/Library/Logs/com.anthropic-bypass.log
```

## Requirements

- macOS with `python3` (ships with Xcode Command Line Tools: `xcode-select --install`).
- Your normal VPN client for the connection itself — this only steers Anthropic
  traffic; it doesn't touch VPN auth.

## How it works

- `anthropic-proxy.py` is an HTTPS `CONNECT` proxy. For bypass hosts
  (`anthropic.com`, `claude.ai`) it opens the upstream socket bound to your
  physical interface (`IP_BOUND_IF`, auto-detected so switching Wi-Fi ↔ Ethernet
  needs no restart). Everything else connects normally.
- For `CONNECT` it only splices bytes — it never sees plaintext and never
  presents a certificate, so Claude Code validates the **real** Anthropic cert.
- Because it acts at the socket layer, the VPN client re-pinning routes every
  few seconds has no effect on it.

## Config

Custom port (if `8888` is taken):

```sh
PROXY_PORT=8899 ./install.sh
```

Bypass list defaults to `anthropic.com` and `claude.ai`; adjust the
`--bypass-host` / `--bypass-cidr` defaults in `anthropic-proxy.py` if needed.
