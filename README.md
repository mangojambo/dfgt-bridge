# DFGT Bridge

DFGT Bridge presents a Logitech Driving Force GT as a virtual Logitech G29 on Linux. It was built for SteamOS and DiRT 5, where the virtual wheel gives Proton a HID device the game recognizes while preserving the DFGT's steering, pedals, buttons, and force feedback path.

**Status:** The owner confirmed the complete SteamOS/DiRT 5 integration and physical force feedback. Offline regression tests exercise protocol, mapping, safety policies, service lifecycle, and installer behavior with simulated devices. Hardware checks are called out separately below; this is not a claim that every effect works in every game.

DFGT Bridge is an independent community project. It is not affiliated with, endorsed by, or sponsored by Logitech. Logitech, Driving Force GT, and G29 are trademarks of their respective owner.

## Features

- Virtual G29 identity through Linux UHID, with input report and descriptor compatibility checked against the preserved v0.8 reference.
- DFGT steering, pedals, button and axis mapping, with a Tk GUI for profiles and presets.
- DiRT 5 Steam launcher that starts the game only after the virtual device and PID force feedback path report ready.
- Optional root-owned systemd backend with a credential-checked local socket, lease, device lock, and isolated runtime.
- Constant-force output to the physical DFGT through Linux EVDEV, with emergency stop, watchdog, bounded effect renewal, and supervised game lifecycle.
- Offline capture replay and deterministic tests. Detailed captures are opt-in.

## Tested status

| Area | Status |
|---|---|
| DiRT 5 recognition, controls, Steam launcher, SteamOS service | Confirmed by the owner on the target SteamOS setup |
| Full-scale physical FFB profile (100%, polarity -1) | Owner approved and tested |
| Offline tests | 479 tests pass in the latest offline run; simulated devices only |
| Installer, socket protocol, GUI widgets, PID lifecycle | Automated tests with fakes; no clean-install VM or fresh SteamOS image was available here |
| Non-DiRT 5 games, other wheels, other architectures | Not validated |
| Fidelity of every PID effect across complete race scenarios | Not established; see [limitations](docs/LIMITATIONS.md) |

The offline suite must never open real `/dev/input`, `/dev/uhid`, hidraw, or USB devices. It does not test motor movement.

## Architecture

```text
DFGT EVDEV input → bridge mapping → UHID virtual G29 → Proton / game
                                      ↑                    ↓
Physical DFGT EVDEV output ← safety supervisor ← HID PIDFF reports
```

The launcher runs as the desktop user. It reads and validates the user's profile, then sends approved settings through a local `SOCK_SEQPACKET` socket authenticated with `SO_PEERCRED`. The privileged service validates those settings again and runs a fixed backend command from a root-owned snapshot. The service does not accept paths, shell commands, or arbitrary arguments over IPC. `ProtectHome=yes` and `ProtectSystem=strict` remain enabled.

The force path supports the EVDEV `FF_CONSTANT` output available on the DFGT driver. The kernel translates that into device-specific commands. This project does not send proprietary Logitech USB commands.

## Requirements

- SteamOS on a Steam Deck or compatible Linux system with systemd.
- Logitech Driving Force GT (`046d:c29a`) supported by the installed Linux Logitech driver.
- Python 3.10 or later, systemd, Tk for the GUI, and existing `uhid` / `hid-universal_pidff` kernel support.
- Root access once to install the restricted service and its systemd socket. The game and launcher run as the normal user.
- The physical DFGT must be accessible to the service. The virtual EVDEV node must be readable and writable by the logged-in user through the system's normal device permissions.

The package has no third-party Python runtime dependencies. Physical output currently targets Linux x86_64 EVDEV ABI.

## Install on SteamOS

Close DiRT 5 and any other force-feedback bridge. From a trusted checkout, run:

```sh
cd /path/to/dfgt-bridge
sudo ./install.sh --test-ready
```

The installer copies only `src/dfgt_bridge` into `/var/lib/dfgt-bridge/runtime`, writes managed systemd units, and creates the user's launcher and profile if needed. It does not start the game or run a motor movement test. `--test-ready` performs a passive virtual-device readiness check; it does not enable physical output. The profile defaults remain 100% strength and maximum, polarity -1, slew 327670, and unlimited session duration. The automatic game integration is enabled for a clean installation and can be disabled in the GUI.

The install command prints the Steam launch option. In Steam, open DiRT 5 → Properties → Launch Options and paste the exact printed line. A typical install for a user named `deck` is:

```text
PROTON_ENABLE_HIDRAW=0x046d/0xc24f "$HOME/.local/bin/dfgt-steam-launch" -- %command%
```

Steam expands environment variables in launch options on supported versions. If it does not on your client, replace `$HOME` with the actual user's home directory. Do not run Proton or the game with `sudo`.

Check readiness without starting DiRT 5:

```sh
"$HOME/.local/bin/dfgt-steam-launch" --check-ready
"$HOME/.local/bin/dfgt-steam-launch" --status
```

The readiness check creates the virtual G29 and verifies PIDFF/EVDEV; it does not authorize physical output. Run it with the wheel connected. The service will stop after the launcher closes.

For an existing installation with a stale socket permission, close the game and reinstall from the updated checkout with `sudo ./install.sh`. This recreates the systemd units and socket while preserving the profile. Run `--check-ready` twice and confirm status returns to `idle` between runs before starting the game. See the [installation guide](docs/INSTALL.md) and [Steam integration notes](docs/STEAM_INTEGRATION.md).

## GUI and button presets

Launch the editor with:

```sh
python3 -B scripts/run-gui.py
```

The GUI defaults to English and also supports Portuguese (Brazil), with a live language switch saved independently from game profiles. It can detect the wheel read-only, preview reports, change axes and button mappings, and save profiles shared with the CLI. Opening the GUI does not activate the motor or create a capture. The default G29 preset maps paddle shifters and handbrake controls to L1/R1; physical codes shared by multiple controls remain indistinguishable. Profiles live under the user's XDG config directory. See [profiles and mappings](docs/PROFILES_AND_MAPPING.md).

## Force feedback settings

The approved profile is:

| Setting | Default |
|---|---:|
| Strength / maximum | 100% / 100% |
| Polarity | -1 |
| Slew rate | 327670 units/s |
| Session duration | Unlimited |
| Virtual identity / PIDFF | G29 / automatic |

F7/F8 adjust strength, F9 latches output to zero, and F10 shows diagnostics. The independent worker watchdog, short EVDEV effect renewal, game-process supervision, and systemd watchdog remain active during an unlimited session. F9 never rearms automatically. Physical output is not used by the offline test backend.

Full-scale force can move the wheel abruptly and pinch or strike hands. Keep hands clear during setup, ensure the wheel is firmly mounted, disconnect other force-feedback software, and keep the emergency stop available. The bridge cannot prevent unrelated software from writing to the same physical wheel; its lock only coordinates with other instances that honor the same lock. Read [FFB safety and supervision](docs/SUPERVISED_FFB.md) before use.

## Troubleshooting

- **Launcher reports permission denied:** update with `sudo ./install.sh`, then run `--check-ready` twice. The service runtime and user-owned socket now use separate directories. Inspect `~/.local/state/dfgt-bridge/steam.log` and `sudo journalctl -u dfgt-bridge.service -n 100 --no-pager`.
- **No `[PRONTO]` message:** confirm the DFGT is connected and recognized, then inspect `sudo systemctl status dfgt-bridge.socket dfgt-bridge.service` and the journal. The launcher will not start the game when bridge startup fails.
- **Reconnect after USB loss:** the reader follows the stable `/dev/input/by-id/` link, not a fixed `eventN`. A disconnect ends the session. Reconnect the wheel and start a new readiness check or game session; the backend does not automatically re-arm.
- **Controls appear twice:** close other virtual wheel or remapping tools and restart the game after the bridge is ready.
- **Stop a session:** run `"$HOME/.local/bin/dfgt-steam-launch" --stop`. Check status with `--status`.

For recovery and log locations, see [troubleshooting and recovery](docs/STEAM_INTEGRATION.md#logs-capture-and-recovery).

## Uninstall

With the game closed:

```sh
cd /path/to/dfgt-bridge
sudo ./uninstall.sh
```

Remove the DiRT 5 Launch Option from Steam. The uninstaller removes managed units, runtime snapshot, and launcher integration while preserving user profiles and logs. It does not unload kernel modules.

## Limitations

- The virtual identity does not make this a physical G29 and does not reproduce Logitech's USB protocol.
- The supported physical output is constant force. Other PID effects are rendered or approximated in software; complete race-effect fidelity has not been established.
- DFGT EVDEV capabilities omit several native condition effects. Inertia and other unsupported effects use approximations.
- PIDFF fallback association may use a global VID/PID rule on some systems; a future physical G29 could be affected until reboot or module reload. See [PIDFF binding limits](docs/PID_BINDING.md).
- USB loss stops the active session; reconnection requires a new session.
- Global gain/autocenter state is not reliably readable or restored. The backend sets these to zero on cleanup.
- Device exclusivity uses a cooperative lock. Other software that ignores it can still write to the wheel.
- Other Linux distributions, kernels, Proton versions, games, and hardware revisions need separate validation.

See [known limitations](docs/LIMITATIONS.md) for the release checklist and validation detail. The native desktop interface is described in the [GUI guide](docs/GUI.md).

## License and attribution

The code is licensed under GPL-2.0-only; see [LICENSE](LICENSE). The original wheel illustrations are CC0-1.0; their provenance is documented in [asset notes](src/dfgt_bridge/assets/README.md). The project is not affiliated with Logitech.
