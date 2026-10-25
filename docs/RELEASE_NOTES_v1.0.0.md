# DFGT Bridge v1.0.0

DFGT Bridge presents a Logitech Driving Force GT as a virtual G29 for SteamOS and DiRT 5. This release candidate packages the tested Steam launcher, restricted systemd backend, GUI profile editor, input mappings, and supervised constant-force output.

The project owner confirmed DiRT 5 recognition, automatic Steam startup, and physical FFB at the approved 100% profile with polarity -1. Offline tests use fake devices and deterministic state; they do not activate a motor. See the README for validation boundaries, setup, limitations, and physical safety notes.

The project owner approved public repository creation and the GPL-2.0-only license. The complete license text is in `LICENSE`; third-party and artwork credits are preserved in the repository.

This release packages the SteamOS and DiRT 5 integration, automatic Steam launcher, restricted systemd backend, bilingual Tk interface, profile and mapping editor, and supervised constant-force output. The owner confirmed DiRT 5 recognition, automatic startup, and physical force feedback using the approved profile: 100% strength and maximum, polarity -1, slew 327670, and unlimited normal session duration.

The offline suite runs against fakes and does not activate a motor. The native Tk smoke test requires a graphical display and was skipped in the headless publication environment. See the README and `docs/LIMITATIONS.md` for validation boundaries, safety guidance, and known limitations.
