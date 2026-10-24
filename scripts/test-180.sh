#!/usr/bin/env bash
set -euo pipefail

HIDRAW_LINK="/dev/input/by-id/usb-046d_Driving_Force_GT-hidraw"

if [[ ! -e "$HIDRAW_LINK" ]]; then
    echo "ERRO: DFGT não conectado."
    exit 1
fi

HIDRAW=$(basename "$(readlink -f "$HIDRAW_LINK")")
RANGE="/sys/class/hidraw/$HIDRAW/device/range"

if [[ ! -f "$RANGE" ]]; then
    echo "ERRO: controle de rotação não encontrado:"
    echo "$RANGE"
    exit 1
fi

ORIGINAL=$(cat "$RANGE")

if [[ ! "$ORIGINAL" =~ ^[0-9]+$ ]]; then
    echo "Valor de rotação inesperado: $ORIGINAL"
    exit 1
fi

echo "Rotação original: ${ORIGINAL}°"
sudo -v

restaurar() {
    echo
    echo "Restaurando rotação para ${ORIGINAL}°..."
    printf '%s\n' "$ORIGINAL" | sudo tee "$RANGE" >/dev/null
}

trap restaurar EXIT

echo "Aplicando 180° ao DFGT..."
printf '180\n' | sudo tee "$RANGE" >/dev/null

ATUAL=$(cat "$RANGE")
echo "Rotação informada pelo driver: ${ATUAL}°"

if [[ "$ATUAL" != "180" ]]; then
    echo "ERRO: driver não aceitou 180°."
    exit 1
fi

python3 ~/dfgt-bridge/dfgt_bridge.py \
    --physical-degrees 180 \
    --steering-degrees 180 \
    --monitor
