#!/bin/bash
# =============================================================================
# Anton OCR Daemon - Service Manager
# Usage: ./manage.sh [start|stop|restart|status|logs]
# =============================================================================

PLIST_LABEL="com.anton.ocrdaemon"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
LOG_FILE="$(dirname "$0")/ocrdaemon.log"
ERR_FILE="$(dirname "$0")/ocrdaemon.err"

GREEN='\033[0;32m'; RED='\033[0;31m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'

cmd="${1:-status}"

case "$cmd" in
    start)
        echo -e "${GREEN}[Anton]${NC} Avvio daemon..."
        launchctl bootstrap gui/$(id -u) "$PLIST_PATH" 2>/dev/null || \
        launchctl load "$PLIST_PATH" 2>/dev/null
        sleep 1
        if launchctl list | grep -q "$PLIST_LABEL"; then
            echo -e "${GREEN}[Anton]${NC} Daemon avviato. ✓"
        else
            echo -e "${RED}[Anton]${NC} Avvio fallito. Controlla: $ERR_FILE"
        fi
        ;;
    stop)
        echo -e "${YELLOW}[Anton]${NC} Arresto daemon..."
        launchctl bootout gui/$(id -u) "$PLIST_PATH" 2>/dev/null || \
        launchctl unload "$PLIST_PATH" 2>/dev/null
        echo -e "${GREEN}[Anton]${NC} Daemon arrestato. ✓"
        ;;
    restart)
        echo -e "${CYAN}[Anton]${NC} Riavvio daemon..."
        launchctl bootout gui/$(id -u) "$PLIST_PATH" 2>/dev/null || \
        launchctl unload "$PLIST_PATH" 2>/dev/null
        sleep 1
        launchctl bootstrap gui/$(id -u) "$PLIST_PATH" 2>/dev/null || \
        launchctl load "$PLIST_PATH" 2>/dev/null
        sleep 1
        echo -e "${GREEN}[Anton]${NC} Daemon riavviato. ✓"
        ;;
    status)
        echo -e "${CYAN}[Anton OCR Daemon — Status]${NC}"
        if launchctl list | grep -q "$PLIST_LABEL"; then
            PID=$(launchctl list | grep "$PLIST_LABEL" | awk '{print $1}')
            echo -e "  Stato  : ${GREEN}RUNNING${NC} (PID: $PID)"
        else
            echo -e "  Stato  : ${RED}STOPPED${NC}"
        fi
        echo -e "  Log    : $LOG_FILE"
        echo -e "  Errors : $ERR_FILE"
        echo ""
        echo -e "${CYAN}[Ultime 5 righe di log]${NC}"
        tail -5 "$LOG_FILE" 2>/dev/null || echo "  (nessun log disponibile)"
        ;;
    logs)
        echo -e "${CYAN}[Anton]${NC} Log in tempo reale (Ctrl+C per uscire)..."
        tail -f "$LOG_FILE"
        ;;
    *)
        echo "Utilizzo: $0 [start|stop|restart|status|logs]"
        exit 1
        ;;
esac
