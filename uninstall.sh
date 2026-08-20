#!/usr/bin/env bash
#
# Remove the Claude Code VPN bypass proxy: stop & delete the background service
# and strip the `claude` wrapper from your shell rc files.
#
# Usage:  ./uninstall.sh
set -euo pipefail

LABEL="com.anthropic-bypass"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
BEGIN="# >>> claude-vpn-bypass >>>"
END="# <<< claude-vpn-bypass <<<"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"

for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
  [ -f "$rc" ] || continue
  if grep -qF "$BEGIN" "$rc"; then
    awk -v b="$BEGIN" -v e="$END" '$0==b{skip=1} !skip{print} $0==e{skip=0}' "$rc" > "$rc.tmp" && mv "$rc.tmp" "$rc"
    echo "  cleaned $rc"
  fi
done

echo "Removed. Open a new terminal for the shell change to take effect."
