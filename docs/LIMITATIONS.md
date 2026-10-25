# Known limitations and validation boundaries

## Confirmed on the target

The project owner confirmed SteamOS startup through Steam, DiRT 5 recognition and the physical force-feedback profile at 100% strength with polarity -1. Those confirmations cover the owner's installation and setup, not every SteamOS image, kernel, Proton build, or wheel revision.

## Verified offline

The 479-test suite covers protocol decoding, mapping, profile validation, lifecycle, safety policy, installer behavior, service IPC, and replay using fake devices and deterministic clocks. It does not open hardware or test motor response. Captures in `tests/fixtures/` are preserved as small regression data; some synthetic vectors are identified as synthetic in their test and documentation contexts.

## Not established

- Complete DiRT 5 race-effect fidelity for every PID effect and game scenario.
- Compatibility with other games, wheels, architectures, kernel versions, or distributions.
- Safety against another privileged process or hardware failure. The lock is cooperative and cannot prevent unrelated writers.
- Restore of prior global DFGT gain/autocenter state. Cleanup sets both to zero.
- Automatic recovery after physical USB disconnection. The current session stops; start a fresh session after reconnecting.
- Race-free availability of a global VID/PID PIDFF fallback on all kernels. Some fallback mechanisms can affect future devices with the same identity until reboot or module reload.
- Clean-install behavior on every SteamOS release. Automated installer validation uses a temporary root and mocked system tools.

## Hardware safety

Physical output can move the wheel strongly and unexpectedly if a game emits force effects. Secure the wheel, keep hands clear during setup, avoid simultaneous force-feedback software, and keep F9 or the independent stop command available. No automated test is allowed to send force to the real device.
