# Open Apply Setup Guide

This guide walks through local installation and first-run setup.

## Prerequisites

1. Python 3.11+
2. Ollama installed and running locally
3. At least one Ollama model pulled
4. Playwright browser runtime installed

## 1) Install

### Option A: pip install (recommended)

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install openapply
```

### Option B: develop from source

```bash
git clone <your-fork-or-repo-url>
cd openapply
python -m venv .venv
.venv\\Scripts\\activate
pip install -e .
```

## 2) Install Playwright browser binaries

```bash
python -m playwright install chromium
```

## 3) Start Ollama and pull recommended models

Example:

```bash
ollama serve
ollama pull llama3.1:8b
ollama pull qwen2.5:14b
```

## 4) Run first-time wizard

```bash
openapply setup
```

The setup wizard will:

1. Check Ollama connectivity and show model list.
2. Create `config.yml` from `config.example.yml` if missing.
3. Help create `cv.md` if missing (paste CV, LinkedIn URL, or notes).
4. Capture target roles, salary range, and location preferences.
5. Initialize local SQLite database.

After successful setup:

```bash
openapply doctor
openapply scan --limit 5 --link-limit 30
```

## Configure Portals Before Scanning

`scan` requires at least one active portal in `portals.yml`.

Setup creates `portals.yml`, but the big-company catalog is shipped **inactive by default** so you choose what to scan.

1) Open `portals.yml`
2) Set at least one entry to `active: true`
3) Re-run:

```bash
openapply doctor
openapply scan --limit 5 --link-limit 30
```

Supported portal types:

- `greenhouse`
- `ashby`
- `lever`
- `linkedin`
- `custom`

## Core Commands

```bash
openapply setup
openapply scan
openapply scan --auto
openapply batch --min-score B --limit 20
openapply apply <url-or-jd-text>
openapply tracker
openapply learn <job-id> <outcome>
```

## What Each Command Does

1. `setup`: bootstraps local environment and config.
2. `scan`: discovers new jobs from active portals.
3. `scan --auto`: discovers + evaluates + queues B+ jobs.
4. `batch`: processes queued URLs in parallel.
5. `apply`: full one-job pipeline with HITL confirmations.
6. `tracker`: interactive dashboard with E/A/L hotkeys.
7. `learn`: logs outcomes and updates scoring weights.

## Data Locations

- Config: `config.yml`
- Base CV: `cv.md`
- Database: `data/openapply.db`
- Queue: `data/pipeline.md`
- Scan log: `data/scan-history.md`
- Generated CVs + cover letters: `output/`
- Evaluation reports: `reports/`

## Troubleshooting

### Ollama unavailable

Symptom:
- command reports Ollama unreachable

Fix:
1. Start Ollama (`ollama serve`)
2. Confirm models exist (`ollama list`)
3. Verify `ollama.base_url` in `config.yml`

### Playwright missing

Symptom:
- command fails when scraping or PDF generation starts

Fix:
1. `pip install -e .`
2. `python -m playwright install chromium`

### Scan finds nothing

Check:
1. `portals.yml` has at least one entry with `active: true`.
2. Portal URLs are reachable.
3. Filters/queries are not overly restrictive.

### Batch queue appears empty

Check `data/pipeline.md` and ensure URLs are under `## Pending` as `- https://...` lines.

## Security and Privacy Notes

- Your CV and generated artifacts stay local.
- Ollama runs locally; no cloud model key is required.
- Application submission is not automated; human review is always required.
