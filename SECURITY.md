# Security Policy

## Supported versions

Only the latest release on [PyPI](https://pypi.org/project/tsubasa/) and the
matching plugin version receive security fixes.

## Reporting a vulnerability

Report vulnerabilities privately via
[GitHub private vulnerability reporting](https://github.com/ramarahmanda/tsubasa/security/advisories/new).
Do not open a public issue for anything exploitable. You should get a first
response within a week.

## What running a captain means

tsubasa is a Claude Code plugin: its skills run inside your Claude Code session
with whatever permissions that session has, and `tsubasa ingest` reads the
repos and documents you point it at. Treat a captain like any other developer
tool with repo access:

- Install from PyPI or a pinned release tag, not from an unreviewed branch.
- The knowledge graph (`.tsubasa/`) contains whatever you fed it — review it
  before committing it to a public repo.
- Skills never require network access or credentials; a fork or modified
  version asking for either is a red flag.
