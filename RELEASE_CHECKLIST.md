# v1.0.0 publication checklist

- [x] Public-facing English README, install guide, release notes, limitations, and test checklist prepared.
- [x] No capture/log blobs in current Git index; `.gitignore` excludes local captures and Python artifacts.
- [x] Reachable history scanned for common private-key and credential patterns; no findings.
- [x] Authenticated GitHub owner checked: `mangojambo`; no existing `dfgt-bridge` repository found in owner list/search.
- [x] Offline suite passed (468); physical devices were not opened.
- [ ] Owner approves license. Existing files declare GPL-2.0-only; add matching top-level license text after approval.
- [ ] Owner reviews public history and confirms repository can be public.
- [ ] Create `mangojambo/dfgt-bridge` as public only after approval.
- [ ] Push prepared conventional Git repository and verify default branch/history.
- [ ] Create v1.0.0 tag and GitHub release from approved commit.
- [ ] Enable GitHub Pages from `docs/` on the default branch; `docs/index.html` is prepared.
- [ ] Run the passive SteamOS readiness and reconnection checklist on the approved release snapshot.

No public repository, push, tag, release, or Pages deployment has been created by this preparation.
