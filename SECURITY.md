# Security Policy

## Supported Versions

Security fixes are provided for the latest release and the `main` branch when possible.

| Version | Supported |
| ------- | --------- |
| Latest release | Yes |
| Older releases | Best effort / no guarantee |
| `main` (development) | Best effort |

## Reporting a Vulnerability

Please report security vulnerabilities **privately** whenever possible.

- **Preferred:** Use GitHub's private vulnerability reporting / Security Advisories for this repository if enabled:
  - <https://github.com/Michaelhehelmy/spms/security/advisories/new>
- **Do not** open a public GitHub issue for vulnerabilities that could expose users, systems, credentials, or sensitive data.
- If private reporting is unavailable, contact the maintainer through the repository owner profile and include only the minimum details needed to establish contact; share full technical details only through a private channel.

When reporting, include (as applicable):
- A clear description of the issue and affected component(s)
- Steps to reproduce or proof-of-concept
- Impact assessment and any prerequisites
- Suggested remediation or mitigation, if known

## What to Expect

- We will make a best-effort attempt to acknowledge reports and assess impact.
- Fix timelines depend on severity, complexity, and maintainer availability.
- Please avoid accessing, modifying, or deleting data that does not belong to you, and avoid disruptive testing against systems you do not own or have permission to test.

## Sensitive Data Guidance

- Never include secrets, passwords, recovery codes, CSRF/session tokens, database contents, guest/payment records, or other private runtime data in reports.
- For non-sensitive bugs, use the in-app/landing-page issue reporting flow or a regular GitHub issue with sanitized diagnostics.
