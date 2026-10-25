# Release test checklist

## Offline checks completed for this preparation

- [x] Full suite: `python3 -B -m unittest discover -s tests -v` (479 tests, all passed).
- [x] Additional no-device guard: `python3 -B -W error::ResourceWarning scripts/test-offline.py` (479 tests, all passed).
- [x] Shell syntax and installer dry-run pass during this preparation; installer tests use temporary roots and mocked systemctl.
- [x] Historical blob scan covered 61 reachable commits for private-key markers, common credential assignments, capture/log files; no matches were found.
- [x] Current Git index contains no captures or log files.
- [ ] Clean SteamOS install on a fresh user account. Prior tests simulate installer behavior; they do not replace this check.
- [ ] Repeat passive readiness twice on target SteamOS and verify socket ownership remains UID of selected account, mode 0600.

## Owner hardware checks before release announcement

- [x] SteamOS and DiRT 5 recognition and automatic launcher integration confirmed by owner.
- [x] Physical full-scale FFB profile approved and tested by owner at 100%, polarity -1.
- [ ] Confirm F9 and `--stop` on the release snapshot.
- [ ] Confirm USB disconnect stops the active session and reconnect requires a fresh session.
- [ ] Confirm button/axis controls and virtual G29 selection after a reboot or SteamOS update.

Do not run motor tests from the automated suite. The remaining hardware checks require an operator at the wheel with the emergency stop available.
