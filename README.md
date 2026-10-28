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

## Import LinkedIn job alerts
```bash
uv run jobfinder alerts            # alerts received in the last 14 days; --days N for more
```
Reads the LinkedIn job-alert emails in a dedicated mailbox over IMAP, read-only (nothing is marked as read, moved or sent), and stores each job in the same DB as the board jobs, keyed `linkedin:alerts:<LinkedIn job id>`. Nothing is fetched from LinkedIn itself: the alert carries title, company, location and a link, but no description. Alert jobs are never closed by a scan, since an alert only lists new jobs.

Setup: copy `.env.example` to `.env` and set `ALERTS_EMAIL_ACCOUNT` and `ALERTS_EMAIL_APP_PASSWORD` (a Gmail app password, never the account password).

## Daily run
`scripts/daily.cmd` runs `jobfinder scan` and then `jobfinder alerts --days 3`, appending everything to `data/logs/daily.log`. A Windows Task Scheduler task runs it every morning; to create one:
```powershell
$repo = (Get-Location).Path
Register-ScheduledTask -TaskName 'job-finder daily scan' `
  -Action (New-ScheduledTaskAction -Execute cmd.exe -Argument "/c `"$repo\scripts\daily.cmd`"" -WorkingDirectory $repo) `
  -Trigger (New-ScheduledTaskTrigger -Daily -At 08:00) `
  -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30))
```
`-StartWhenAvailable` runs a missed morning run as soon as the computer is back on.

## Label the eval set
```bash
uv run jobfinder label
```
Shows stored jobs one at a time; answer `y` (fit) or `n` (not fit) and say why, `s` to skip, `o` to open the posting, `b` to go back, `q` to quit. Every answer is saved at once to `data/labels.jsonl` (personal, not committed) with a full snapshot of the job, so a later session continues where you stopped and the eval set survives jobs closing. Stops at `--target` (default 80) fit / not-fit labels.

If `data/suggestions.jsonl` exists (personal, not committed), jobs with a suggested label come first and show it: press Enter to accept the suggestion with its reason, or `y` / `n` to override it with your own reason. Each label records what was suggested, and the session ends with how many suggestions you accepted and changed.

Jobs are offered in a fixed order (`--seed`) that keeps the eval set about 30% email-alert jobs (mostly junior, title only), 40% board jobs the pre-filter keeps, 20% Israel jobs the filter rejects and 10% jobs outside Israel. That way it has enough "fit" labels to measure recall, is not dominated by the obvious rejects, and still catches the filter's mistakes.

## Pre-filter
```bash
uv run jobfinder filter                # summary, plus a check against your labels
uv run jobfinder filter --show kept    # or: rejected, unplaced
```
Drops jobs that are clearly out before the extractor sees them: the location must name a place in Israel (a location like "Remote" that names no place is kept), and titles with Senior / Lead / Manager / Staff / Director / ... are rejected unless they also say student, intern or junior. Experience and days on site are left to the extractor. With labels, it lists every job you labeled fit that the filter rejects: those are filter mistakes.

## Try an extractor
```bash
uv run jobfinder extract                          # Gemini (GEMINI_API_KEY in .env), 5 jobs
uv run jobfinder extract --extractor ollama --limit 10 --model qwen2.5:32b
```
Runs an extractor on the first jobs of the labeling order and prints what it reads from each posting: `years_required`, `onsite_days`, `is_student`, `location` and `stack`, plus the tokens spent. Answers are saved in `data/jobs.db` (table `extractions`, one per job and extractor) and reused while the posting and the prompt stay the same, so a rerun costs nothing; `--no-cache` asks the model again. Both extractors get the same instructions and JSON schema (`src/jobfinder/extraction/base.py`), and only the public posting text is sent, never your priorities. A busy model (HTTP 503) or a per-minute quota (429) is retried after 5, 20 and 60 seconds.

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
