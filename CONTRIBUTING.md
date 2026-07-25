Thank you for contributing to `queuectl` — this project aims to be small,
well-tested, and easy to review. Follow these guidelines to get your PR
accepted quickly.

Getting started
-------------

- Create a virtual environment and install dev dependencies:

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\Activate.ps1 on Windows
python -m pip install -e .[dev]
```

Running tests & linters
-----------------------

Run tests with:

```bash
python -m pytest -q
```

Fix formatting with Black and lint with Ruff:

```bash
python -m black .
python -m ruff . --fix
```

Commit style
------------

- Keep changes focused and add tests for behavior changes.
- Run tests and linters locally before opening a PR.

PR process
----------

- Open a PR against `main` with a clear description and link to any
  relevant issue. CI will run tests and linters automatically.
