# SteamOS installation guide

This guide covers the SteamOS installation validated by the project owner. Commands that touch hardware are marked by their purpose; no motor test is part of installation.

## Before you start

Connect the DFGT directly, close DiRT 5, and stop other wheel bridges or force-feedback tools. Use the normal SteamOS user account. Keep the wheel clear of hands while connecting it because other software may retain global force-feedback state.

## Install

From a trusted project checkout:

```sh
cd /path/to/dfgt-bridge
sudo ./install.sh --test-ready
```

The installer checks required system tools and kernel modules, validates the saved profile, installs a fixed root-owned code snapshot and restricted systemd units, and writes a per-user Steam launcher. A clean installation enables automatic integration for the game; the GUI can disable it later. The readiness probe is passive and does not run the game or physical output worker.

The installer prints the exact Steam launch option using the selected account's home path. Add that line to DiRT 5 → Properties → Launch Options. Typical example:

```text
PROTON_ENABLE_HIDRAW=0x046d/0xc24f "/home/<user>/.local/bin/dfgt-steam-launch" -- %command%
```

Replace `<user>` with the Linux account name. Do not use sudo in Steam launch options.

## Verify without motor output

With the wheel connected:

```sh
"$HOME/.local/bin/dfgt-steam-launch" --check-ready
"$HOME/.local/bin/dfgt-steam-launch" --status
"$HOME/.local/bin/dfgt-steam-launch" --check-ready
"$HOME/.local/bin/dfgt-steam-launch" --status
```

Each readiness check should report `[PRONTO]`; status should return to `idle` after cleanup. This checks virtual-device creation, PIDFF binding, EVDEV capabilities, and user access. It does not test torque, effects, or the game itself.

## Update

Close the game before updating. From the newer checkout, run `sudo ./install.sh`. The installer refuses to update while a bridge session holds its device lock. It preserves profile data and the GUI's automation preference. After an update, repeat the two passive readiness checks above.

In v1.1.0, Spring ×3 is the default for new profiles and profiles without an
explicit Spring value. Reinstalling migrates old profiles while preserving
custom gains, including ×1 and the previous per-family Spring field. Other
default effect gains remain ×1; strength stays 100%/100%, polarity −1, slew
327670 and duration unlimited. Launch the game normally with the existing
Steam Launch Options; no additional gain argument or JSON edit is needed.

## Configure Steam

Paste the launch option printed by the installer. The launcher waits for device readiness before starting Proton, keeps Steam and the game unprivileged, and stops output when the game exits. If automatic integration is disabled in the GUI, the game runs without the bridge.

## GUI

Run `python3 -B scripts/run-gui.py` in Desktop Mode. Detection is read-only. Profiles are stored under the account's XDG config directory and are shared with command-line tools. Opening or editing a profile does not activate FFB.

## Remove

Close the game and run:

```sh
cd /path/to/dfgt-bridge
sudo ./uninstall.sh
```

Remove the Launch Option from Steam. The uninstaller preserves profiles and logs and does not unload modules.

## Logs and recovery

The per-user launcher log is `~/.local/state/dfgt-bridge/steam.log`; privileged service logs are in `/var/log/dfgt-bridge/` and the system journal. Check units with:

```sh
sudo systemctl status dfgt-bridge.socket dfgt-bridge.service dfgt-bridge-modules.service
sudo journalctl -u dfgt-bridge.service -u dfgt-bridge-modules.service -n 100 --no-pager
```

For a stale socket or error 13, close the game, reinstall with `sudo ./install.sh`, then repeat readiness twice and confirm the socket remains owned by the configured user with mode 600. See [Steam recovery notes](STEAM_INTEGRATION.md).
