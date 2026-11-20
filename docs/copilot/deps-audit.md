# Dependency audit — 2026-11-19

This was a report-only audit. No packages were installed or upgraded.

## Python

Command: `.\.venv\Scripts\python.exe -m pip list --outdated --format=columns`

The only outdated installed package reported was `pip` itself:

| Package | Installed | Latest | Recommendation |
|---|---:|---:|---|
| `pip` | 26.1.2 | 26.3 | Upgrade during a planned environment refresh; this is tooling, not an application dependency. |

No application package was reported as outdated by the local environment. `backend/requirements.txt` uses minimum-version ranges rather than a lock file, so a reproducible dependency snapshot is not currently recorded.

## npm

Command: `npm outdated --no --long`

No outdated packages were reported.

Command: `npm audit --offline --json`

The cached audit completed with zero reported vulnerabilities: 0 info, 0 low, 0 moderate, 0 high, and 0 critical across 191 dependency entries.

Because the audit was offline, this is not a substitute for a later online advisory refresh. No upgrade is recommended from this run.

## Follow-up

The safest future maintenance step is to refresh the Python and npm advisory data in a controlled environment, then update only with a reviewed lockfile or patch-level security fix followed by the backend and web test suites.
