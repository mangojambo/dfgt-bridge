# Changelog

## Unreleased (candidate v1.0.1)

- Let Steam launch the original game command without starting DFGT Bridge when the DFGT is absent; confirm hardware identity through the stable input link and read-only sysfs.
- Handle a confirmed unplug during bridge startup while keeping permission, PIDFF, service, and session failures visible.
- Preserve the Steam/Proton environment and argv in fallback mode; keep `--check-ready` as an explicit hardware diagnostic.

## v1.0.0 — 2026-10-25

First public release.

- Present the DFGT as a virtual G29 through UHID with automatic PIDFF readiness checks.
- Add Steam launcher and restricted systemd service with authenticated local IPC, session lease, device lock, and isolated runtime directory.
- Add supervised EVDEV constant-force output with emergency stop, watchdog, and full-scale approved profile defaults.
- Add GUI editor, XDG profile store, game presets, input mapping preview, deterministic capture replay, and offline test suite.
- Fix systemd runtime ownership collision by keeping the root backend runtime separate from the user-owned control socket.
- Include original CC0 wheel illustrations and installation/recovery documentation.

No changes to the approved FFB math, 100% strength, polarity -1, slew 327670, or unlimited normal session duration are part of release preparation.
