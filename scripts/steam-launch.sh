#!/bin/sh
# Sem eval/$*: %command% e cada argumento permanecem separados.
exec /usr/bin/python3 -I -B "$(dirname "$(readlink -f "$0")")/../src/dfgt_bridge/steam_main.py" "$@"
