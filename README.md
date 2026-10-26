# job-finder

Opportunity radar for my job search: pulls postings from public ATS job-board APIs (with full descriptions), scores them against my priorities in code, and sends a daily Telegram digest.

Status: **Phase 1 done** — collection works; scoring (Phase 2) and the Telegram digest (Phase 3) are next.

## Collect jobs
```bash
uv run jobfinder scan
```
Reads the boards in `data/sources.toml` (Greenhouse and Ashby public APIs, one request per board), stores every open job with its full description in `data/jobs.db`, and prints what is new and what closed. A board that cannot be read is reported and its stored jobs stay open until the next successful scan.

Exit code: `0` every board was read, `1` some board failed, `2` config error.

Acceptance check (2026-10-26): 10 consecutive scans of 25 boards, 1,450 jobs, 0 duplicate keys, every job with a description.

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
