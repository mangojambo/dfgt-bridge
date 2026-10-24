#!/usr/bin/env bash
# DFGT Bridge launcher - SteamOS / DiRT 5
set -u

DFGT_HIDRAW='/dev/input/by-id/usb-046d_Driving_Force_GT-hidraw'
BRIDGE="$HOME/dfgt-bridge/dfgt_bridge.py"
LOG="$HOME/dfgt-bridge/launch-dirt5.log"

if (( $# == 0 )); then
    echo 'Uso: launch-dirt5.sh COMANDO_DO_JOGO [ARGUMENTOS...]' >&2
    exit 2
fi

# Sem o DFGT, o jogo segue normalmente sem emulacao.
if [[ ! -e "$DFGT_HIDRAW" ]]; then
    exec "$@"
fi

if [[ ! -f "$BRIDGE" ]]; then
    echo "DFGT Bridge nao encontrado: $BRIDGE" >&2
    exit 1
fi

HIDRAW=$(basename "$(readlink -f "$DFGT_HIDRAW")")
RANGE="/sys/class/hidraw/$HIDRAW/device/range"

if [[ ! -r "$RANGE" || ! -w "$RANGE" ]]; then
    echo "Sem acesso ao ajuste de rotacao: $RANGE" >&2
    exit 1
fi

ORIGINAL=$(cat "$RANGE")
if [[ ! "$ORIGINAL" =~ ^[0-9]+$ ]]; then
    echo "Rotacao original invalida: $ORIGINAL" >&2
    exit 1
fi

BRIDGE_PID=''
cleanup() {
    trap - EXIT INT TERM HUP
    if [[ -n "$BRIDGE_PID" ]]; then
        kill "$BRIDGE_PID" 2>/dev/null || true
        wait "$BRIDGE_PID" 2>/dev/null || true
    fi
    if [[ -w "$RANGE" ]]; then
        printf '%s\n' "$ORIGINAL" > "$RANGE" \
            || echo "Nao foi possivel restaurar $ORIGINAL graus" >&2
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

printf '180\n' > "$RANGE" || exit 1
if [[ "$(cat "$RANGE")" != '180' ]]; then
    echo 'O driver nao aceitou a rotacao de 180 graus' >&2
    exit 1
fi

: > "$LOG"
python3 -u "$BRIDGE" --physical-degrees 180 --steering-degrees 180 >> "$LOG" 2>&1 &
BRIDGE_PID=$!

READY=0
for (( i=0; i<60; i++ )); do
    if grep -q '^Xbox virtual:' "$LOG"; then
        READY=1
        break
    fi
    if ! kill -0 "$BRIDGE_PID" 2>/dev/null; then
        break
    fi
    sleep 0.1
done

if [[ "$READY" != 1 ]]; then
    echo "Falha ao iniciar o DFGT Bridge. Veja $LOG" >&2
    tail -n 20 "$LOG" >&2
    exit 1
fi

# Tempo curto para o Steam/udev enumerar o controle antes do Proton iniciar.
sleep 1
"$@"
exit $?
