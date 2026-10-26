# job-finder — context for coding agents

## What this is
An "opportunity radar": collect postings from public sources -> extract structured fields -> score in code against `priorities.txt` -> Telegram digest -> (later) assisted apply.
First module of Jarvis. Plan: Obsidian vault `02_Projects/Jarvis/Jarvis - Roadmap.md`.

## Current phase
Phase 0 — feasibility. `tools/ats_probe.py` maps companies to ATS (Greenhouse / Lever / Ashby / SmartRecruiters via public read APIs; Comeet / Workday via careers-page fingerprint).

## Hard rules
- Read-only public job-board APIs only. No scraping or automation of LinkedIn, Drushim, AllJobs, Indeed.
- Applications are never submitted without explicit human approval.
- Never commit secrets or personal data (.env, CV, priorities.txt, DB). See .gitignore.
- The engine is domain-generic: anything job-specific belongs in config / scoring, not in the source or storage layers.

## Engineering standards
- Python >= 3.11, full type hints, `mypy --strict` clean, no placeholder comments.
- Tests with pytest in `tests/`; network calls are never made in unit tests.
- Commands: `pip install pytest mypy`, then `pytest` and `mypy` from the repo root.
