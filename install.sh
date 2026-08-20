#!/usr/bin/env bash
#
# One-button install for the Claude Code VPN bypass proxy.
#
# Some corporate VPNs route api.anthropic.com through an SSL-inspection proxy
# and re-pin that route every few seconds, so Claude Code fails with
# "Self-signed certificate detected" and route-table fixes lose the race. This
# installs a tiny local proxy that pins ONLY Anthropic traffic to your physical
# NIC (IP_BOUND_IF) — it egresses your normal internet connection with the real
# Anthropic certificate, no interception — as a launchd service (auto-start,
# self-heal), and points the `claude` command at it. Everything else keeps its
# normal path through the tunnel.
#
# Usage:  ./install.sh          (no sudo — per-user setup)
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.anthropic-bypass"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/$LABEL.log"
PORT="${PROXY_PORT:-8888}"
PY="$(command -v python3 || true)"

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }

[ -n "$PY" ] || { echo "python3 not found. Install Xcode Command Line Tools:  xcode-select --install" >&2; exit 1; }
[ -f "$here/anthropic-proxy.py" ] || { echo "anthropic-proxy.py missing — run from the repo root." >&2; exit 1; }

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && ! pgrep -f "anthropic-proxy.py .*:$PORT" >/dev/null 2>&1; then
  echo "Port $PORT is in use by another program. Re-run with a different port:  PROXY_PORT=8899 ./install.sh" >&2
  exit 1
fi

say "1/3  background service"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>            <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PY</string>
        <string>$here/anthropic-proxy.py</string>
        <string>--listen</string><string>127.0.0.1:$PORT</string>
        <string>--iface</string><string>auto</string>
    </array>
    <key>RunAtLoad</key>        <true/>
    <key>KeepAlive</key>        <true/>
    <key>StandardOutPath</key>  <string>$LOG</string>
    <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load -w "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null || true
ok "installed & started"

say "2/3  shell wrapper (routes only \`claude\` through the proxy)"
BEGIN="# >>> claude-vpn-bypass >>>"
END="# <<< claude-vpn-bypass <<<"
add_wrapper() {
  local rc="$1"
  [ -e "$rc" ] || return 0
  if grep -qF "$BEGIN" "$rc"; then
    awk -v b="$BEGIN" -v e="$END" '$0==b{skip=1} !skip{print} $0==e{skip=0}' "$rc" > "$rc.tmp" && mv "$rc.tmp" "$rc"
  fi
  cat >> "$rc" <<RC

$BEGIN
claude() { HTTPS_PROXY="http://127.0.0.1:$PORT" command claude "\$@"; }
$END
RC
  ok "updated $rc"
}
case "${SHELL:-}" in *zsh) touch "$HOME/.zshrc" ;; *bash) touch "$HOME/.bashrc" ;; esac
add_wrapper "$HOME/.zshrc"
add_wrapper "$HOME/.bashrc"

say "3/3  verify"
sleep 1
code="$(curl -x "http://127.0.0.1:$PORT" -s --max-time 12 -o /dev/null -w '%{http_code}' https://api.anthropic.com/v1/messages 2>/dev/null || echo 000)"
if [ "$code" != "000" ]; then
  ok "proxy reaches Anthropic (HTTP $code, real cert)"
else
  warn "could not reach Anthropic through the proxy yet — check $LOG"
fi

echo
say "Done — open a NEW terminal and just run:  claude"
echo "  • The VPN can stay ON. Nothing to start or babysit."
echo "  • Health check:  curl -x http://127.0.0.1:$PORT -s https://api.anthropic.com/v1/messages -o /dev/null -w '%{http_code}\\n'"
echo "  • Uninstall:     ./uninstall.sh"
