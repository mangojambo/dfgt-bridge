# DFGT Bridge v1.1.0

DFGT Bridge presents the Logitech Driving Force GT as a virtual G29 for SteamOS and DiRT 5. This release improves supervised force feedback across normal loading and race transitions, adds Spring calibration controls, and improves output diagnostics.

## What changed

- Spring ×3 is now the official default for new installations and profiles without an explicit Spring setting. Existing custom profile values, including legacy per-family Spring values, are preserved during migration. Other effect gains remain ×1.
- Legitimate idle calculation gaps can recover after fresh, valid commands. F9, keyboard loss, serious faults, and emergency latches retain their explicit recovery protections.
- F5 selects Spring ×3 and F6 selects ×4 temporarily, with a smooth 300 ms transition. F7/F8 continue adjusting overall strength; F9 stops output; F10 reports calibration, commanded EVDEV levels, peak and saturation statistics. F5/F6 do not modify saved profiles.
- The GUI Force Feedback section has a Spring slider from ×1.00 to ×4.00 in ×0.25 steps. It loads and saves the selected profile. Saved changes apply at the next session and do not alter a running bridge.
- Latch diagnostics retain the original trigger, and offline physical-policy traces verify the new selection behavior. Steam still launches the original game command without starting the bridge when the DFGT is absent.

## Updating on SteamOS

From the v1.1.0 checkout, close DiRT 5 and run:

```sh
cd ~/dfgt-bridge
sudo ./install.sh
```

Open DiRT 5 from Steam with the existing Launch Options. No JSON edits or extra Spring arguments are needed. Profiles with no explicit Spring value migrate to ×3; a custom value such as ×1.25 or ×3.5 remains unchanged. The user's personal selection is not imposed on other users.

## Validation and limits

The owner manually reports that ×2 improved centering, ×3 improved it further, and ×4 worked but felt little different from ×3. The owner also exercised F5/F6 and saved ×3 with the GUI. These are observations on one SteamOS/DiRT 5 setup, not torque measurements, a comparison with measured G920 torque, or universal compatibility certification.

The offline suite has 574 tests: 573 pass and one native Tk smoke test is skipped without a display. It uses fake devices and does not activate a motor. The project still does not establish complete fidelity for every PID effect or compatibility with every game, kernel, wheel, and system. The force output uses the supported EVDEV constant-force path; EVDEV commands are not torque measurements. Keep the wheel mounted and emergency stop available. See [known limitations](LIMITATIONS.md), [installation](INSTALL.md), and [supervised FFB](SUPERVISED_FFB.md).

The approved physical defaults remain 100% strength and maximum, polarity −1, slew 327670 units/s, and unlimited normal session duration. F9, watchdog, final EVDEV limit, and the supervised service remain active.
