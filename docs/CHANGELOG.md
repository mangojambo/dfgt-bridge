# Changelog

## v1.1.0 — 2026-10-26

- Set the official Spring default to ×3 for new installs and profiles without an explicit Spring gain; preserve explicit user values and legacy migration.
- Recover force feedback after legitimate idle calculation gaps and normal race/loading transitions while keeping F9, keyboard-loss, emergency, and hard-fault latches.
- Add independent PID effect gains, live Spring F5 ×3 / F6 ×4 selectors with a 300 ms interpolation, and F10 EVDEV saturation/peak diagnostics.
- Add a bilingual GUI Spring slider from ×1 to ×4 in ×0.25 steps. Saved changes apply to the next session; the slider does not reconfigure an active session.
- Improve latch evidence and trace replay, and retain Steam fallback when the DFGT is disconnected.
- Owner-reported SteamOS/DiRT 5 checks: ×3 improved centering over ×2; ×4 worked with little perceptible difference from ×3; F5/F6 and the GUI slider were exercised. These are user observations, not torque measurements or universal compatibility claims.

Upgrade with `sudo ./install.sh` from the v1.1.0 checkout. Existing profiles that have no explicit Spring gain migrate to ×3; custom Spring values are preserved. Existing Steam Launch Options remain unchanged.

Offline suite: 574 tests, 573 passed, one native Tk smoke test skipped without a display. No physical test is part of the automated suite.

## v1.0.1 — 2026-10-26

- Let Steam launch the original game command without starting DFGT Bridge when the DFGT is absent; confirm hardware identity through the stable input link and read-only sysfs.
- Handle a confirmed unplug during bridge startup while keeping permission, PIDFF, service, and session failures visible.
- Preserve the Steam/Proton environment and argv in fallback mode; keep `--check-ready` as an explicit hardware diagnostic.
- Clarify the no-bridge log message for both automatic fallback and explicitly disabled integration.

## v1.0.0 — 2026-10-25

First public release.

- Present the DFGT as a virtual G29 through UHID with automatic PIDFF readiness checks.
- Add Steam launcher and restricted systemd service with authenticated local IPC, session lease, device lock, and isolated runtime directory.
- Add supervised EVDEV constant-force output with emergency stop, watchdog, and full-scale approved profile defaults.
- Add GUI editor, XDG profile store, game presets, input mapping preview, deterministic capture replay, and offline test suite.
- Fix systemd runtime ownership collision by keeping the root backend runtime separate from the user-owned control socket.
- Include original CC0 wheel illustrations and installation/recovery documentation.
