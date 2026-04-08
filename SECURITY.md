# Security Policy

## Supported Versions

Security fixes are prioritized for the latest release line.

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |
| < 0.1.0 | No |

## Reporting a Vulnerability

Please do not open public issues for undisclosed security vulnerabilities.

Preferred path:

1. Use GitHub Security Advisories for this repository, if enabled.
2. If advisories are not available, open a private maintainer contact request issue with minimal details and ask for a secure reporting channel.

Include:

- Affected component(s)
- Steps to reproduce
- Potential impact
- Suggested remediation (if known)

## Response Expectations

- Initial triage acknowledgement target: within 7 days.
- Status updates target: at least every 14 days while actively triaging.
- Fix timelines vary by severity, complexity, and maintainer availability.

## Scope Notes for openapply

This project is local-first. Security-sensitive areas include:

- Local data handling (`config.yml`, `cv.md`, `data/openapply.db`, generated artifacts)
- Prompt and model invocation boundaries
- Browser automation and form-drafting logic
- Dependency supply-chain risk

## Disclosure Policy

- Coordinate disclosure with maintainers before public release of exploit details.
- After a fix is available, maintainers may publish a summary and upgrade guidance.
