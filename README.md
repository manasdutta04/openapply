# openapply

> Autonomous job search agent. Runs 100% locally. Your CV never leaves your machine.

openapply is an open-source, privacy-first, terminal-first job application assistant.
It helps you discover roles, score fit, tailor ATS-safe CVs, and draft applications with Human In The Loop controls.

## Why

career-ops showed the world this is possible.
openapply makes it accessible to everyone:

- No paid cloud AI subscription requirement
- No cloud API key requirement
- No vendor lock-in
- Full local control over data and models

## What It Does

openapply is designed as an end-to-end local pipeline:

1. Job discovery across configured portals
- scans active portals and discovers new listings
- deduplicates against existing DB records and scan history

2. 10-dimension fit evaluation
- scores each role from 1.0 to 5.0 across role, skills, seniority, compensation, geo, stage, PMF, growth, interview likelihood, and timeline
- computes weighted score + grade (A/B/C/D/F)
- writes a markdown report to reports/

3. Tailored ATS CV generation
- extracts high-value JD keywords
- reorders bullets by relevance (without deleting user content)
- renders ATS-safe HTML to PDF via Playwright
- saves artifacts to output/ and metadata to DB

4. Human-reviewed application drafting
- opens and drafts common form fields
- never auto-submits
- requires explicit human review before marking as applied

5. Parallel batch processing
- processes queued URLs concurrently
- fault tolerant: one failure does not block the queue
- resumable behavior for previously processed jobs

6. Outcome learning loop
- logs interview/rejection/offer/ghosted outcomes
- adjusts scoring weights in DB based on feedback patterns

## Quick Start

```bash
pip install openapply
openapply setup
```

Then:

```bash
openapply scan
```

### Required: enable at least 1 portal
`scan` requires at least one active portal in `portals.yml` (setup creates it for you, but everything is disabled by default).

1) Open `portals.yml`  
2) Set at least one entry to `active: true`  
3) Run:

```bash
openapply doctor
openapply scan --limit 5 --link-limit 30
```
## How To Test (Recommended)

### 1) Developer install (this repo)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
python -m playwright install chromium
```

Run quick health checks:

```bash
openapply doctor
```

Run unit tests:

```bash
python -m unittest discover -s tests -v
```

### 2) User install (PyPI / TestPyPI)

Once you publish (see “Publish To PyPI” below), verify in a clean environment:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install openapply
openapply setup
openapply doctor
```

### 3) End-to-end CLI smoke test

- Ensure `portals.yml` exists (setup creates it) and set at least one portal to `active: true`.
- Scan:

```bash
openapply scan --limit 5 --link-limit 30
```

- Run auto-pipeline for a known job URL:

```bash
openapply pipeline <job-url>
```

- Open tracker TUI:

```bash
openapply tracker
```

## Philosophy

AI analyzes. You decide. HITL always.

openapply intentionally avoids full autonomy on final submission actions.
The system can evaluate, draft, and prefill, but a human must review before applying.

## Core Commands

```bash
openapply setup
openapply doctor
openapply scan
openapply scan --auto
openapply batch --min-score B --limit 20
openapply pipeline <url-or-jd-text>
openapply apply <url-or-jd-text>
openapply tracker
openapply learn <job-id> <outcome>
```

## Models

openapply uses local Ollama models. A practical starting setup:

- `llama3.1:8b` for evaluation
	- Pros: fast, low hardware footprint, good for scoring and routing
	- Tradeoff: less nuanced generation quality for long-form text

- `qwen2.5:14b` for generation
	- Pros: stronger CV/cover-letter drafting quality
	- Tradeoff: slower and heavier resource usage

General guidance:

- Use smaller models for high-volume scan/eval loops.
- Use larger models for final artifact generation quality.
- Keep all inference local through Ollama.

## Local-First Privacy

- CV, config, reports, and generated files are stored locally.
- SQLite DB runs locally (`data/openapply.db`).
- Prompt and model execution are local-first by design.

## Project Status

openapply is in active development (`0.1.1`) and currently optimized for terminal workflows.
Web UI support is planned as secondary priority.

## Documentation

- docs/README.md (documentation index)
- docs/SETUP.md
- docs/ARCHITECTURE.md
- docs/PUBLISH.md (maintainers: publish to PyPI)
- CONTRIBUTING.md
- SECURITY.md
- CODE_OF_CONDUCT.md

## Community

To help keep the project healthy and maintainable:

- Read `CONTRIBUTING.md` before opening pull requests.
- Review `SECURITY.md` for vulnerability reporting guidance.
- Follow `CODE_OF_CONDUCT.md` in all project spaces.

## Publish To PyPI

Use this flow to publish releases so users can run:

```bash
pip install openapply
openapply setup
```

Build and validate:

```bash
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
```

Upload to TestPyPI first:

```bash
python -m twine upload --repository testpypi dist/*
```

Test install from TestPyPI in a clean environment:

```bash
python -m pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple openapply
```

Upload to PyPI:

```bash
python -m twine upload dist/*
```

PowerShell helper:

```powershell
./scripts/publish.ps1
./scripts/publish.ps1 -TestPyPI
./scripts/publish.ps1 -PyPI
```

## Inspiration

Inspired by the ideas popularized in career-ops (MIT), including structured fit scoring, CV templating workflow, and portal scanning patterns.
openapply is implemented from scratch as a standalone Python product.

## License

MIT