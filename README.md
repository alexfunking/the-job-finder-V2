# job-finder

Opportunity radar for my job search: pulls postings from public ATS job-board APIs (with full descriptions), scores them against my priorities in code, and sends a daily Telegram digest.

Status: **Phase 1 done** — collection works. Phase 2 (scoring) is in progress: the eval-set labeling tool and the deterministic pre-filter are in; extraction and scoring are next, then the Telegram digest (Phase 3).

## Collect jobs
```bash
uv run jobfinder scan
```
Reads the boards in `data/sources.toml` (Greenhouse and Ashby public APIs, one request per board), stores every open job with its full description in `data/jobs.db`, and prints what is new and what closed. A board that cannot be read is reported and its stored jobs stay open until the next successful scan.

Exit code: `0` every board was read, `1` some board failed, `2` config error.

Acceptance check (2026-10-26): 10 consecutive scans of 25 boards, 1,450 jobs, 0 duplicate keys, every job with a description.

## Label the eval set
```bash
uv run jobfinder label
```
Shows stored jobs one at a time; answer `y` (fit) or `n` (not fit) and say why, `s` to skip, `o` to open the posting, `b` to go back, `q` to quit. Every answer is saved at once to `data/labels.jsonl` (personal, not committed) with a full snapshot of the job, so a later session continues where you stopped and the eval set survives jobs closing. Stops at `--target` (default 80) fit / not-fit labels.

Jobs are offered in a fixed order (`--seed`) that keeps the eval set about 70% Israel jobs, 20% Israel jobs with senior titles and 10% jobs outside Israel, so it is not dominated by the obvious rejects.

## Pre-filter
```bash
uv run jobfinder filter                # summary, plus a check against your labels
uv run jobfinder filter --show kept    # or: rejected, unplaced
```
Drops jobs that are clearly out before the extractor sees them: the location must name a place in Israel (a location like "Remote" that names no place is kept), and titles with Senior / Lead / Manager / Staff / Director / ... are rejected unless they also say student, intern or junior. Experience and days on site are left to the extractor. With labels, it lists every job you labeled fit that the filter rejects: those are filter mistakes.

## Find a company's board
```bash
uv run python tools/ats_probe.py data/companies.csv --out data/ats_map.csv
```
Maps companies to their ATS. Add a `slugs` value for boards the guesser cannot produce (e.g. `wizinc`, `monday.com`) and a `careers_url` for companies that come back `unknown`. Then add the board to `data/sources.toml` by hand.

## Dev
```bash
uv sync
uv run pytest
uv run mypy
```
