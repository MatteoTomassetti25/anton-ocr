#!/bin/bash
# =============================================================================
# Anton OCR Daemon - Service Manager
# Usage: ./manage.sh [start|stop|restart|status|logs]
# =============================================================================

PLIST_LABEL="com.anton.ocrdaemon"
PLIST_SRC="$(dirname "$0")/com.anton.ocrdaemon.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
LOG_FILE="$(dirname "$0")/ocrdaemon.log"
ERR_FILE="$(dirname "$0")/ocrdaemon.err"
DOMAIN="gui/$(id -u)"

GREEN='\033[0;32m'; RED='\033[0;31m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
OLLAMA_HOST="http://localhost:11434"
MODEL="glm-ocr:latest"

_is_running() {
    launchctl print "${DOMAIN}/${PLIST_LABEL}" &>/dev/null
}

_bootout() {
    # Try modern API first, fall back to legacy
    launchctl bootout "${DOMAIN}" "${PLIST_DEST}" 2>/dev/null || \
    launchctl unload "${PLIST_DEST}" 2>/dev/null || true
}

_bootstrap() {
    cp "${PLIST_SRC}" "${PLIST_DEST}"
    launchctl bootstrap "${DOMAIN}" "${PLIST_DEST}" 2>/dev/null || \
    launchctl load "${PLIST_DEST}" 2>/dev/null
}

_unload_vram() {
    if curl -s --max-time 5 "${OLLAMA_HOST}/api/tags" &>/dev/null; then
        curl -s --max-time 10 -X POST "${OLLAMA_HOST}/api/generate" \
            -d "{\"model\": \"${MODEL}\", \"keep_alive\": 0}" &>/dev/null
        echo -e "  ${CYAN}[VRAM]${NC} Memoria Ollama deallocata (${MODEL} scaricato)."
    fi
}

cmd="${1:-status}"

case "$cmd" in

    start)
        if _is_running; then
            echo -e "${YELLOW}[Anton]${NC} Il daemon è già in esecuzione."
            exit 0
        fi
        echo -e "${GREEN}[Anton]${NC} Avvio daemon..."
        _bootstrap
        sleep 2
        if _is_running; then
            PID=$(launchctl print "${DOMAIN}/${PLIST_LABEL}" 2>/dev/null | grep "pid" | awk '{print $3}')
            echo -e "${GREEN}[Anton]${NC} Daemon avviato. ✓ (PID: ${PID})"
        else
            echo -e "${RED}[Anton]${NC} Avvio fallito. Controlla: ${ERR_FILE}"
            tail -5 "${ERR_FILE}" 2>/dev/null
            exit 1
        fi
        ;;

    stop)
        if ! _is_running; then
            echo -e "${YELLOW}[Anton]${NC} Il daemon non è in esecuzione."
            exit 0
        fi
        echo -e "${YELLOW}[Anton]${NC} Arresto daemon..."
        _unload_vram
        _bootout
        # Wait up to 5s for clean shutdown
        for i in {1..5}; do
            _is_running || break
            sleep 1
        done
        if _is_running; then
            echo -e "${RED}[Anton]${NC} Arresto forzato (SIGKILL)..."
            PID=$(launchctl print "${DOMAIN}/${PLIST_LABEL}" 2>/dev/null | grep "pid" | awk '{print $3}')
            kill -9 "${PID}" 2>/dev/null || true
        fi
        echo -e "${GREEN}[Anton]${NC} Daemon arrestato. ✓"
        ;;

    restart)
        echo -e "${CYAN}[Anton]${NC} Riavvio daemon..."
        _unload_vram
        _bootout
        sleep 1
        _bootstrap
        sleep 2
        if _is_running; then
            echo -e "${GREEN}[Anton]${NC} Daemon riavviato. ✓"
        else
            echo -e "${RED}[Anton]${NC} Riavvio fallito. Controlla: ${ERR_FILE}"
            exit 1
        fi
        ;;

    status)
        echo -e "${CYAN}━━━ Anton OCR Daemon — Status ━━━${NC}"
        if _is_running; then
            PID=$(launchctl print "${DOMAIN}/${PLIST_LABEL}" 2>/dev/null | grep "pid" | awk '{print $3}')
            echo -e "  Stato   : ${GREEN}● RUNNING${NC} (PID: ${PID})"
        else
            echo -e "  Stato   : ${RED}○ STOPPED${NC}"
        fi
        echo -e "  Log     : ${LOG_FILE}"
        echo -e "  Errori  : ${ERR_FILE}"
        echo ""
        echo -e "${CYAN}[Ultime 5 righe di log]${NC}"
        tail -5 "${LOG_FILE}" 2>/dev/null || echo "  (nessun log)"
        ;;

    logs)
        echo -e "${CYAN}[Anton]${NC} Log in tempo reale (Ctrl+C per uscire)..."
        tail -f "${LOG_FILE}"
        ;;

    *)
        echo "Utilizzo: $0 [start|stop|restart|status|logs]"
        exit 1
        ;;
esac
