# v1.0.0 publication checklist

- [x] Public-facing English README, install guide, release notes, limitations, and test checklist prepared.
- [x] No capture/log blobs in current Git index; `.gitignore` excludes local captures and Python artifacts.
- [x] Reachable history scanned for common private-key and credential patterns; no findings.
- [x] Authenticated GitHub owner checked: `mangojambo`; no existing `dfgt-bridge` repository found in owner list/search.
- [x] Offline suite passed (483 cases: 482 passed, 1 native Tk smoke test skipped without a display); physical devices were not opened.
- [x] Owner approved GPL-2.0-only; matching top-level LICENSE file added.
- [ ] Owner reviews public history and confirms repository can be public.
- [ ] Create `mangojambo/dfgt-bridge` as public only after approval.
- [ ] Push prepared conventional Git repository and verify default branch/history.
- [ ] Create v1.0.0 tag and GitHub release from approved commit.
- [ ] Enable GitHub Pages from `docs/` on the default branch; `docs/index.html` is prepared.
- [ ] Run the passive SteamOS readiness and reconnection checklist on the approved release snapshot.

No public repository, push, tag, release, or Pages deployment has been created by this preparation.
