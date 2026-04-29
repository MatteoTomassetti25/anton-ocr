#!/bin/bash
# =============================================================================
# Anton OCR Daemon - Uninstaller
# =============================================================================
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(uname -s)"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${CYAN}[INFO]${NC}  $1"; }
ok()   { echo -e "${GREEN}[OK]${NC}    $1"; }

if [ "$OS" = "Darwin" ]; then
    PLIST="$HOME/Library/LaunchAgents/com.anton.ocr.plist"
    info "Rimozione LaunchAgent..."
    launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    ok "LaunchAgent rimosso."
else
    info "Rimozione systemd service..."
    systemctl --user disable --now anton-ocr.service 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/anton-ocr.service"
    systemctl --user daemon-reload
    ok "Systemd service rimosso."
fi

info "Rimozione virtualenv..."
rm -rf "$REPO_DIR/venv"
ok "Virtualenv rimosso."

echo ""
echo -e "${GREEN}Anton OCR Daemon disinstallato.${NC}"
echo -e "I file in ${CYAN}~/anton-ocr/${NC} sono stati mantenuti."
echo ""
