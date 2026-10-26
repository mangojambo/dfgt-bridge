# DFGT Bridge v1.0.1

DFGT Bridge v1.0.1 adds a safe Steam launch fallback for systems where the Logitech Driving Force GT is not connected.

## What's changed

- The launcher checks the stable DFGT input link and confirms VID:PID `046d:c29a` through read-only sysfs before loading profiles or contacting the privileged service.
- If the wheel is absent, Steam's original Proton command starts without creating the virtual G29 or starting the bridge/FFB service. The log records `DFGT not connected — launching game without bridge`.
- A disconnect during bridge startup falls back only when the startup diagnostic identifies the missing DFGT and a second check confirms it is absent. Permission, PIDFF, service, and session failures remain errors.
- `--check-ready` remains an explicit diagnostic and continues to report missing hardware.
- The no-bridge log now accurately describes both automatic fallback and explicitly disabled integration.

The project owner validated both paths on SteamOS: DiRT 5 starts without the bridge when the DFGT is disconnected, and the bridge continues to work when the DFGT is connected.

No force-feedback math, approved defaults, mappings, watchdog, driver behavior, or emergency-stop behavior changed. The approved profile remains 100% strength and maximum, polarity -1, slew 327670, and unlimited normal session duration.

## Validation

The offline suite ran 494 tests: 493 passed and one native Tk smoke test was skipped because the test environment had no graphical display. Automated tests use simulated devices and do not activate a motor. No physical motor tests were run as part of this release preparation.

For installation and physical safety guidance, see the [README](../README.md), [installation guide](INSTALL.md), and [FFB supervision guide](SUPERVISED_FFB.md).
