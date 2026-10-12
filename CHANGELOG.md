# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-10-11

### Added

- `Polyzon` (sync, thread-safe) and `AsyncPolyzon` (asyncio) clients for the
  Polyzon API: `search`, `search_all`, `get_model`, `get_models`, `me`, `limits`.
- Typed, frozen results with lenient parsing and response metadata (request id,
  daily rate-limit window).
- Exception hierarchy rooted at `PolyzonError`, mapped from HTTP status and error code.
- Automatic retries with `Retry-After` support (capped at 60 seconds) and jittered
  exponential backoff.
- Lazy pagination, bounded-concurrency batch fetching with short-circuit on fatal errors.
- Explicit and environment proxies (HTTP, HTTPS, SOCKS via the `socks` extra),
  custom `httpx` clients, `with_options` derived clients sharing one pool.
- Security defaults: token validation and masking, HTTPS-only base URLs (HTTP only
  for loopback), no redirects, 8 MiB response cap.

[Unreleased]: https://github.com/PolyzonOrg/polyzon-python/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/PolyzonOrg/polyzon-python/releases/tag/v1.0.0
