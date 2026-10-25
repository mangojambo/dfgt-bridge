# License and attribution

The owner approved `GPL-2.0-only`. A top-level `LICENSE` file now contains the complete GNU GPL version 2 text, matching existing Python SPDX headers and `pyproject.toml`. Approval of this license does not authorize public repository creation or push.

## License decision

The approved code license is **GPL-2.0-only**, with a complete GPL version 2 text in `LICENSE`. The preserved v0.8 HID descriptor reference explicitly attributes its descriptor source to `userspace/logi-wheel` under GPL-2.0-only, and the maintained implementation carries the same SPDX declaration. GPL-2.0-only is consistent with those existing notices. The historical code also describes the MIT-licensed `uhid-sim-relay` as an inspiration; the maintained package has no direct third-party Python runtime dependencies and does not vendor that project.

This is a provenance and compatibility record, not legal advice. Review the derived descriptor and source attribution before changing notices. The historical source mentions do not independently establish permission to relicense derived material under a more permissive license.

## Runtime and development dependencies

- Runtime: Python standard library, Linux kernel interfaces, systemd, and Tkinter supplied by the OS. `pyproject.toml` declares no third-party Python dependency.
- Development/test: Python `unittest`; no third-party test framework is declared.
- The installer calls system-provided `/usr/bin/systemctl`, `/usr/bin/modprobe`, `/usr/bin/modinfo`, and Python. It does not package these tools.
- No vendored third-party library directory is present.

## Artwork and trademarks

`src/dfgt_bridge/assets/dfgt-front.svg` and `g29-front.svg` are original schematic illustrations released under CC0-1.0, as stated in the SVGs and asset README. The GitHub Pages copies carry the same CC0 notice. They contain no manufacturer photographs or logos. Product names identify compatible devices; DFGT Bridge is not affiliated with Logitech.
