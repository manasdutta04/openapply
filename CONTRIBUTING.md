# Contributing to openapply

Thanks for your interest in improving openapply.
This project is local-first, privacy-first, and human-in-the-loop by design.

## Ground Rules

- Be respectful and constructive in issues and pull requests.
- Keep user data local and private; avoid introducing cloud-only assumptions.
- Preserve HITL safety controls: never add auto-submit behavior for job applications.
- Prefer small, focused pull requests over large mixed changes.

## Development Setup

1. Fork and clone the repository.
2. Create and activate a virtual environment.
3. Install the package in editable mode.
4. Install Playwright browser dependencies.

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
python -m playwright install chromium
```

macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m playwright install chromium
```

## Running Tests

Run the current test suite:

```bash
python -m unittest discover -s tests -v
```

Before opening a PR, run at least the tests related to your changes.

## Code Style

- Target Python 3.11+ compatibility.
- Follow existing naming and module structure.
- Keep functions small and explicit.
- Add or update docstrings where behavior is non-obvious.
- Avoid unrelated refactors in the same PR.

## Documentation

If behavior or commands change, update docs in the same PR:

- `README.md` for user-facing usage changes
- `docs/SETUP.md` for onboarding/install updates
- `docs/ARCHITECTURE.md` for structural/runtime changes

## Commit and PR Guidance

- Use clear commit messages that describe intent.
- Include a concise PR description:
  - What changed
  - Why it changed
  - How you tested it
- Link related issues when applicable.
- Include terminal output or screenshots for CLI/TUI UX changes.

## Security-Related Changes

If your PR touches security-sensitive behavior (scraping, form handling, local storage, dependency updates), call it out explicitly in the PR description and mention any risk tradeoffs.

## Need Help?

- Open a GitHub issue for bugs, ideas, or questions.
- For sensitive vulnerabilities, follow `SECURITY.md`.
