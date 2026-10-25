# Desktop GUI

The application is implemented with Python's standard Tkinter and ttk widgets. It has four sections: Home, Mapping, Settings, and Diagnostics. The profile selector and Save changes action stay in the top bar; Undo and Redo are available there as well.

## Languages

New installations start in English. Select Português (Brasil) from the language control to switch immediately. The preference is saved separately in `ui.json` beside the existing game profile file, so locale changes do not edit a profile or alter force-feedback settings. Existing profile and integration files remain unchanged. Invalid or unknown locale preferences fall back to English.

All application-owned interface text uses stable catalog keys in `src/dfgt_bridge/i18n.py`. English is the fallback catalog; the catalog test checks full `pt-BR` coverage. Technical identifiers, JSON keys, EVDEV codes, button labels, CLI flags, commands, paths, and protocol fields retain their stable forms. The Diagnostics page displays bounded raw runner output for troubleshooting; those backend-provided records may retain their original wording.

## Profiles and mapping

The profile manager supports loading, creating, duplicating, renaming, and deleting profiles. Saving is disabled until an editable, persistable value changes. Language changes do not mark a profile dirty.

Mapping supports the complete existing button set, axis selection, presets, read-only DFGT monitoring, input detection, conflict resolution, dial direction swap, and Undo/Redo. Wheel diagrams are accompanied by keyboard-accessible controls and an input list. Profile edits do not change an active runner.

## Force feedback and safety

The main Settings view keeps FFB strength visible; maximum strength, polarity, slew, and duration are grouped under Advanced. Approved values remain 100% strength, 100% maximum, polarity -1, slew 327670, and unlimited normal session duration. Physical FFB starts only through an explicit GUI action or the configured Steam launcher. Opening or editing the GUI does not open a physical output device or create a capture. F9, the independent stop command, effect renewal bound, worker watchdog, and game supervision remain available. See [supervised FFB](SUPERVISED_FFB.md).

## Screenshot validation

Capture review should use the native application at 1280×800 and 1600×900 with KDE scaling at 100%, 125%, and 150%. Switch to both `en` and `pt-BR`, inspect Home, Mapping, Settings, and Diagnostics, and verify that all controls remain reachable, labels do not clip, keyboard focus is visible, and the mapping inspector remains scrollable. Do not connect or monitor a physical wheel for this layout review. This sandbox has no accessible display server, so no real application screenshots are included in this change.

`tests/test_gui_tk_integration.py` creates a real Tk root when a display is available, builds all four pages, changes locale in both directions, and destroys the window. Run it under a virtual display with `xvfb-run -a python3 -B -m unittest discover -s tests -p 'test_gui_tk_integration.py' -v` where Xvfb is installed. In headless environments without Xvfb, the test is skipped; `test_shell_builds_every_panel_in_its_frame_not_the_profile_identifier` provides a mock-based parent-widget contract check but cannot validate native rendering or geometry.
