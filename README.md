# job-finder

Opportunity radar for my job search: pulls postings from public ATS job-board APIs (with full descriptions), scores them against my priorities in code, and sends a daily Telegram digest.

Status: **Phase 0 — feasibility.**

## Phase 0: map companies to their ATS
```bash
python tools/ats_probe.py data/companies.csv --out data/ats_map.csv
```
Edit `data/companies.csv` first. Add `careers_url` for companies that come back `unknown` (Comeet, Workday and others have no slug-based public API, so they are detected from the careers page).

## Dev
```bash
pip install pytest mypy
pytest
mypy
```
