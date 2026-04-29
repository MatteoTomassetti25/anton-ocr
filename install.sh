#!/bin/bash
# =============================================================================
# Anton OCR Daemon - Installer (macOS & Debian/Ubuntu Linux)
# =============================================================================
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$REPO_DIR/venv"
CONFIG_FILE="$REPO_DIR/config.env"
LOG_DIR="$REPO_DIR/logs"
MODEL="glm-ocr:latest"

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $1"; }
ok()      { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

OS="$(uname -s)"
info "Sistema rilevato: $OS"
mkdir -p "$LOG_DIR"

# ---- 1. Detect & Install Ollama ----
info "Verifica Ollama..."
if ! command -v ollama &>/dev/null; then
    warn "Ollama non trovato. Installazione in corso..."
    curl -fsSL https://ollama.com/install.sh | sh
    ok "Ollama installato."
else
    ok "Ollama già presente: $(ollama --version)"
fi

# ---- 2. Start Ollama service ----
info "Avvio servizio Ollama..."
if [ "$OS" = "Darwin" ]; then
    # macOS: Ollama runs as a background app
    open -a Ollama 2>/dev/null || true
    sleep 3
else
    # Linux: use systemd or run in background
    if systemctl is-active --quiet ollama 2>/dev/null; then
        ok "Ollama service già in esecuzione."
    else
        if systemctl list-unit-files ollama.service &>/dev/null; then
            sudo systemctl enable --now ollama
        else
            ollama serve &>/dev/null &
            sleep 3
        fi
    fi
fi

# Wait for Ollama to be ready
for i in {1..10}; do
    if curl -s http://localhost:11434/api/tags &>/dev/null; then
        ok "Ollama API pronta."; break
    fi
    sleep 2
done

# ---- 3. Pull glm-ocr model ----
info "Download modello $MODEL (potrebbe richiedere qualche minuto)..."
ollama pull "$MODEL"
ok "Modello $MODEL pronto."

# ---- 4. Install system dependencies ----
info "Installazione dipendenze di sistema..."
if [ "$OS" = "Darwin" ]; then
    if ! command -v brew &>/dev/null; then
        error "Homebrew non trovato. Installa Homebrew prima: https://brew.sh"
    fi
    brew install poppler python@3 2>/dev/null || true
    ok "Dipendenze macOS installate."
else
    sudo apt-get update -qq
    sudo apt-get install -y python3 python3-pip python3-venv poppler-utils libpoppler-cpp-dev
    ok "Dipendenze Linux installate."
fi

# ---- 5. Create Python virtualenv & install packages ----
info "Creazione virtualenv Python..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install watchdog pdf2image ollama requests pillow -q
ok "Dipendenze Python installate."

# ---- 6. Setup config.env if not present ----
if [ ! -f "$CONFIG_FILE" ]; then
    warn "config.env non trovato, uso configurazione di default."
    cp "$REPO_DIR/config.env.example" "$CONFIG_FILE" 2>/dev/null || true
fi

# Create default directories
mkdir -p "$HOME/anton-ocr/input" "$HOME/anton-ocr/output" "$HOME/anton-ocr/elaborati"
ok "Cartelle di lavoro create in ~/anton-ocr/"

# ---- 7. Install service ----
if [ "$OS" = "Darwin" ]; then
    # ---- macOS: LaunchAgent ----
    PLIST_SRC="$REPO_DIR/launchd/com.anton.ocr.plist"
    PLIST_DEST="$HOME/Library/LaunchAgents/com.anton.ocr.plist"

    # Inject correct paths into plist
    sed "s|__REPO_DIR__|$REPO_DIR|g; s|__VENV_DIR__|$VENV_DIR|g" \
        "$PLIST_SRC" > "$PLIST_DEST"

    launchctl bootout gui/$(id -u) "$PLIST_DEST" 2>/dev/null || true
    launchctl bootstrap gui/$(id -u) "$PLIST_DEST"
    ok "LaunchAgent installato e avviato."

else
    # ---- Linux: systemd user service ----
    SERVICE_DIR="$HOME/.config/systemd/user"
    SERVICE_SRC="$REPO_DIR/systemd/anton-ocr.service"
    SERVICE_DEST="$SERVICE_DIR/anton-ocr.service"

    mkdir -p "$SERVICE_DIR"
    sed "s|__REPO_DIR__|$REPO_DIR|g; s|__VENV_DIR__|$VENV_DIR|g" \
        "$SERVICE_SRC" > "$SERVICE_DEST"

    systemctl --user daemon-reload
    systemctl --user enable --now anton-ocr.service
    ok "Systemd service installato e avviato."
fi

echo ""
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo -e "${GREEN}  Anton OCR Daemon installato con successo!${NC}"
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo ""
echo -e "  📂 Cartella input  : ${CYAN}~/anton-ocr/input${NC}"
echo -e "  📄 Output Markdown : ${CYAN}~/anton-ocr/output${NC}"
echo -e "  🗃  Archivio PDF   : ${CYAN}~/anton-ocr/elaborati${NC}"
echo ""
echo -e "  ➡  Trascina un PDF in ${CYAN}~/anton-ocr/input${NC} per avviare l'OCR."
echo ""
