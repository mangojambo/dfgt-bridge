# SPDX-License-Identifier: GPL-2.0-only
"""Contexto de falhas de I/O, sem conteúdo de perfis, argv ou ambiente."""
from contextlib import contextmanager
import os


def io_error(exc, operation, path=None):
    target = os.fspath(path if path is not None else exc.filename) if (path is not None or exc.filename) else '(sem caminho)'
    # Nunca registrar nomes de contas ou subcaminhos privados do home.
    if target.startswith(('/home/', '/root/')):
        target = '<home privado>/' + os.path.basename(target)
    return f'origem={operation}; caminho={target}; errno={exc.errno}; {os.strerror(exc.errno) if exc.errno else type(exc).__name__}'


@contextmanager
def operation(name, path=None):
    try:
        yield
    except OSError as exc:
        raise OSError(exc.errno, io_error(exc, name, path)) from exc
