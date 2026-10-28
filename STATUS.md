# Status — read this first in a new session

Last updated: 2026-10-28. Update this file at the end of every working session.

Source of truth for the plan: Obsidian vault `C:\Users\alex0\Vault\02_Projects\Jarvis\Jarvis - Roadmap.md`.
Design and ADRs: `C:\Users\alex0\Vault\02_Projects\Job Finder\Job Finder - Architecture.md` (ADR-001 to ADR-007).

## Where we are
**Phase 2 (measurable scoring), steps 1–2 of 7, plus a new source: LinkedIn job alerts by email.** The labeling tool, the deterministic pre-filter and `jobfinder alerts` are built; the hand-labeled eval set does not exist yet (`data/labels.jsonl` is missing), so the filter is not measured yet.

Repo: https://github.com/alexfunking/the-job-finder-V2 (public), `main` tracks `origin/main` and is pushed up to `1794dd5` (the daily scheduled run); check `git status -sb`. 243 tests pass, `mypy --strict` clean.

**Daily run (since 2026-10-28):** Windows Task Scheduler task `job-finder daily scan` runs `scripts/daily.cmd` at 08:00 (or as soon as possible after a missed start): `jobfinder scan`, then `jobfinder alerts --days 3`. Output is appended to `data/logs/daily.log` (gitignored). Check that log to verify closing / reopening across days.

## Done
- **Phase 0 — ATS mapping** (`tools/ats_probe.py`): 58 companies probed. 25 have a public API (21 Greenhouse, 4 Ashby) with 408 Israel jobs. Comeet (9 companies) deferred, ADR-002. 14 large companies have no public API (Workday or in-house).
- **Phase 1 — collection** (`jobfinder scan`): Greenhouse and Ashby sources, SQLite store with first_seen / last_seen / closed_at, boards in `data/sources.toml`. Acceptance check: 10 runs, 1,450 jobs, 0 duplicate keys, every job has a full description.
- **More boards (2026-10-28):** 9 more companies from a second probe run; 34 boards, ~1,600 open jobs (1,625 with alerts on 2026-10-28; the filter keeps 190). Little gain: board companies rarely post student / junior roles.
- **LinkedIn alerts source (2026-10-28, ADR-008)** (`jobfinder alerts`): reads LinkedIn job-alert emails from a dedicated Gmail mailbox used only for alerts (~2,000 alerts since 2025-04) over read-only IMAP. Credentials live only in `.env` (`ALERTS_EMAIL_ACCOUNT`, `ALERTS_EMAIL_APP_PASSWORD`, an app password). The parser handles English and Hebrew alerts (296/296 jobs on a 53-alert sample). Alert jobs have no description (`description_text=None`, DB schema v2) and are never closed. First import (14 days): 18 jobs, all pass the filter, mostly junior / graduate roles at companies without an API (NVIDIA, Mobileye, Intuit, Dell, Check Point).
- **Phase 2.1 — labeling tool** (`jobfinder label`): shows one job at a time; `y` / `n` with a required reason, `s` skip, `o` open, `b` back, `q` quit. Appends to `data/labels.jsonl` (gitignored, personal) with a full job snapshot. Seeded order: 70% Israel, 20% Israel senior titles, 10% outside Israel.

## Next steps
1. **User:** label 80 jobs with `uv run jobfinder label` (~2 hours, can be split across sessions). Check in on progress: count labels and how many are "fit" (fewer than 15 fit makes recall noisy).
2. **Done (2026-10-28):** deterministic pre-filter, `jobfinder filter` (`filtering.py`). Location must name a place in Israel (unplaced "Remote" is kept); senior title words rejected unless student / intern / junior. Does not read the description. On the real DB: 162 of 1,450 open jobs kept. Measure it once labels exist: "fit, rejected" must be 0.
3. **Code, next:** `Extractor` interface: `years_required`, `onsite_days`, `is_student`, `location`, `stack`.
4. Two implementations: `ApiExtractor` and `OllamaExtractor` (local) — ADR-006. For now the API one is **Gemini**: the key is in `.env` as `GEMINI_API_KEY` (gitignored, verified 2026-10-28 by listing models). Claude Haiku 5.5 is added later as another implementation, so keep the interface provider-neutral. Pick by eval: go local if within 3–5 precision points.
5. Score computed in code from `priorities.txt` (not by the model).
6. `jobfinder eval` printing precision and recall. Phase 2 is done when goals G1 and G2 hold on the eval set.
7. Then Phase 3: daily Telegram digest, including a weekly watchlist of careers pages for companies without an API (ADR-007), and grouping of logical duplicates (same company + title + location, 16 groups seen).

## Open tasks on the user's side
- Label the eval set (above).
- Create an account at console.anthropic.com, load credit, create an API key (no longer blocking: Gemini is used meanwhile).
- Archive the old repo `alexfunking/The_job_Finder`.

## Known gaps
- Alert jobs never close (an alert lists only new jobs). Phase 3 must show only recently first-seen alert jobs, or expire them by age.
- Alert jobs have no description, so the extractor and scoring work from the title only for them.
- Only LinkedIn alerts are parsed. AllJobs / Drushim alerts need their own parser once the user subscribes them to the same mailbox.
- Only 2 student and 1 junior titles exist across the 25 boards: the eval set may get few "fit" labels, and the sources may not match a student / junior search. Raise with the user after labeling.
- The old repo `alexfunking/The_job_Finder` is public and contains `priorities.txt` (personal). Archiving does not hide it; making it private does.
- Closing and reopening jobs is verified only in unit tests; real behavior shows after days of daily scans (running since 2026-10-28, see `data/logs/daily.log`).
- The labeling order draws 70% from board jobs, which have almost no student / junior roles, while the alert jobs are nearly all junior. Consider putting alert jobs and filter-kept jobs first, and backfilling alerts beyond 14 days (~2,000 since 2025-04), so the eval set has enough "fit" labels to measure G2.
- The same job can arrive from a board and from a LinkedIn alert under different keys: Phase 3 grouping must work across sources.
- Cost of the Haiku extractor is a rough estimate (under $1 for the whole DB); measure on the eval set.
- Workday terms of use not checked yet (after the MVP, ADR-007).

## How to work with the user
- Talk in Hebrew; code, commits and repo docs in English.
- Work step by step: build one step, open the files in the editor, explain, and wait for approval before committing and moving on.
- Commit only after the user approves; push only when asked.
- After each step, update the vault (roadmap progress, ADRs in the architecture note, decision log in `CLAUDE_INDEX.md`). Vault prose is Hebrew and each paragraph starts with a Hebrew word (RTL plugin). obsidian-git backs the vault up; do not commit there.
- Git identity in this repo is `Alex <alexfunking@users.noreply.github.com>`; never put the user's Gmail in the repo.
