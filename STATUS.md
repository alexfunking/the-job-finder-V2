# Status — read this first in a new session

Last updated: 2026-10-28. Update this file at the end of every working session.

Source of truth for the plan: Obsidian vault `C:\Users\alex0\Vault\02_Projects\Jarvis\Jarvis - Roadmap.md`.
Design and ADRs: `C:\Users\alex0\Vault\02_Projects\Job Finder\Job Finder - Architecture.md` (ADR-001 to ADR-007).

## Where we are
**Phase 2 (measurable scoring), steps 1–2 of 7.** The labeling tool and the deterministic pre-filter are built; the hand-labeled eval set does not exist yet (`data/labels.jsonl` is missing), so the filter is not measured yet.

Repo: https://github.com/alexfunking/the-job-finder-V2 (public), `main` tracks `origin/main`. Last pushed `f4f57dc`; the pre-filter is committed locally, not pushed yet. 214 tests pass, `mypy --strict` clean.

## Done
- **Phase 0 — ATS mapping** (`tools/ats_probe.py`): 58 companies probed. 25 have a public API (21 Greenhouse, 4 Ashby) with 408 Israel jobs. Comeet (9 companies) deferred, ADR-002. 14 large companies have no public API (Workday or in-house).
- **Phase 1 — collection** (`jobfinder scan`): Greenhouse and Ashby sources, SQLite store with first_seen / last_seen / closed_at, boards in `data/sources.toml`. Acceptance check: 10 runs, 1,450 jobs, 0 duplicate keys, every job has a full description.
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
- Only 2 student and 1 junior titles exist across the 25 boards: the eval set may get few "fit" labels, and the sources may not match a student / junior search. Raise with the user after labeling.
- The old repo `alexfunking/The_job_Finder` is public and contains `priorities.txt` (personal). Archiving does not hide it; making it private does.
- Closing and reopening jobs is verified only in unit tests; real behavior shows after days of daily scans.
- Cost of the Haiku extractor is a rough estimate (under $1 for the whole DB); measure on the eval set.
- Workday terms of use not checked yet (after the MVP, ADR-007).

## How to work with the user
- Talk in Hebrew; code, commits and repo docs in English.
- Work step by step: build one step, open the files in the editor, explain, and wait for approval before committing and moving on.
- Commit only after the user approves; push only when asked.
- After each step, update the vault (roadmap progress, ADRs in the architecture note, decision log in `CLAUDE_INDEX.md`). Vault prose is Hebrew and each paragraph starts with a Hebrew word (RTL plugin). obsidian-git backs the vault up; do not commit there.
- Git identity in this repo is `Alex <alexfunking@users.noreply.github.com>`; never put the user's Gmail in the repo.
