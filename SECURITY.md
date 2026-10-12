# Security policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 1.x     | yes       |
| < 1.0   | no        |

Security fixes are released as patch versions of the latest minor release.

## Reporting a vulnerability

Email **security@polyzon.org**. Please include the SDK version, Python version,
a description of the issue and, if possible, a minimal reproduction. We
acknowledge reports within three working days and keep you informed until the fix
is released. We are happy to credit you in the release notes.

Please **do not** open a public GitHub issue, discussion or pull request for a
vulnerability, and never post in public:

- API tokens (`pzk_…`) — yours or anyone else's. If a token leaked, revoke it at
  https://polyzon.org/account/ and create a new one;
- proxy credentials, logs or tracebacks containing them;
- details of an unpatched vulnerability.

## Scope

This repository covers the Python SDK. Issues in the API itself
(api.polyzon.org) or the Polyzon website are welcome at the same address.
