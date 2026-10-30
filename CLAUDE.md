# job-finder — context for coding agents

## What this is
An "opportunity radar": collect postings from public sources -> extract structured fields -> score in code against `priorities.txt` -> Telegram digest -> (later) assisted apply.
First module of Jarvis. Plan: Obsidian vault `02_Projects/Jarvis/Jarvis - Roadmap.md`.

## Start here
Read `STATUS.md` at the start of every session: where the work stands, what is next, and how the user likes to work. Update it at the end of every session.

## Current phase
Phase 1 done (collection: `jobfinder scan` → `data/jobs.db`). Phase 2 in progress: `jobfinder label` builds the hand-labeled eval set in `data/labels.jsonl`; extraction and scoring are next. Design and ADRs: vault `02_Projects/Job Finder/Job Finder - Architecture.md`.
- `src/jobfinder/` is the package: `models.Job` is the source-independent model; `sources/` has one module per ATS; `storage.JobStore` owns the DB; `scan.py` orchestrates; `cli.py` is the entry point; `geo.py` has location matching; `labels.py` / `sampling.py` / `labeling.py` are the eval-set labeling tool; `filtering.py` is the deterministic pre-filter; `extraction/` has the model extractors and their DB cache; `scoring.py` scores jobs from `priorities.txt` in code; `matches.py` + `ui.py` + `ui_page.html` are the local matches page (`jobfinder ui`); `label_desk.py` + `ui_label.html` are its labeling screen (`/label`); `evaluation.py` compares the pipeline with the labels (`jobfinder eval`); `alerts.py` + `mailbox.py` + `sources/linkedin_alerts.py` import LinkedIn job-alert emails (read-only IMAP).
- Boards to scan live in `data/sources.toml` (hand-maintained, committed).
- `tools/ats_probe.py` is the Phase 0 tool that maps companies to ATS; keep it working, it is re-run when companies change.
- Comeet is deferred (ADR-002): its careers API uses company-issued tokens with no third-party permission. Do not call it.

## Hard rules
- Read-only public job-board APIs only. No scraping or automation of LinkedIn, Drushim, AllJobs, Indeed. Reading the job-alert emails these sites send to the user's own mailbox is allowed (ADR-008); following their links to fetch pages is not.
- Never write the user's email addresses or other personal details into tracked files (the repo is public).
- Applications are never submitted without explicit human approval.
- Never commit secrets or personal data (.env, CV, priorities.txt, DB, labels.jsonl). See .gitignore.
- The engine is domain-generic: anything job-specific belongs in config / scoring, not in the source or storage layers.

## Engineering standards
- Python >= 3.11, full type hints, `mypy --strict` clean, no placeholder comments.
- Tests with pytest in `tests/`; network calls are never made in unit tests.
- Environment is managed by uv (Python pinned in `.python-version`): `uv sync`, then `uv run pytest` and `uv run mypy` from the repo root.
