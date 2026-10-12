# Contributing

Thanks for helping! Bug reports and pull requests are welcome. For security issues
follow [SECURITY.md](SECURITY.md) instead of opening an issue.

## Development setup

```sh
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev,socks]"
```

## Checks

Every pull request must pass the same checks as CI:

```sh
ruff check .
ruff format --check .
mypy                            # strict mode, configured in pyproject.toml
pytest                          # fails below 90 % coverage
pip-audit --strict .            # known vulnerabilities in runtime dependencies
python -m build && twine check --strict dist/*
```

The test suite never calls the real API: it uses `httpx.MockTransport` and local
servers on 127.0.0.1. Keep it that way.

## Pull requests

- One logical change per pull request, with tests and docstrings.
- Use [Conventional Commits](https://www.conventionalcommits.org/) for titles,
  e.g. `fix: honour Retry-After dates`, `feat: add ...`, `docs: ...`.
- Update `CHANGELOG.md` under `[Unreleased]` for user-visible changes, and both
  `README.md` and `README.ru.md` when behaviour or the public API changes.
- Do not add runtime dependencies besides `httpx`.
