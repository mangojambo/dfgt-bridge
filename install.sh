#!/bin/sh
# Autorização inicial somente; nenhum sudoers é criado.
exec /usr/bin/python3 -I -B "$(dirname "$(readlink -f "$0")")/scripts/install-project.py" "$@"
