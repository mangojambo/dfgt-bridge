#!/usr/bin/env python3
"""Executa a suite com audit hook que rejeita opens reais de dispositivos."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def reject_devices(event, args):
    if event != 'open' or not isinstance(args[0], (str, bytes, os.PathLike)):
        return
    path = os.path.realpath(os.fsdecode(args[0]))
    if path == '/dev/uhid' or path == '/dev/input' or path.startswith((
            '/dev/input/', '/dev/hidraw', '/dev/bus/usb/')):
        raise RuntimeError('Teste offline tentou abrir dispositivo: ' + path)


if __name__ == '__main__':
    sys.addaudithook(reject_devices)
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    raise SystemExit(not result.wasSuccessful())
