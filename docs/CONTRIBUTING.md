# Contributing

Thanks for helping improve MinusPod. Bug reports, feature requests, and pull
requests are welcome. For a larger change, open an issue first so the approach
can be discussed before implementation.

## Development prerequisites

- **OS:** Linux is the primary development and CI environment. macOS can be
  used for development if the native audio dependencies below are available.
  On Windows, use WSL2 with a Linux distribution.
- **Python:** Python 3.12. This is the version used by CI and the container
  image.
- **uv:** Recommended for creating the virtual environment and installing
  Python dependencies. `pip` can be used instead.
- **Node.js and npm:** Node.js 22 and its bundled npm are used by CI for the
  frontend.
- **SQLite:** The application uses SQLite through Python's standard-library
  `sqlite3` module; a separate SQLite server is not needed. The `sqlite3`
  command-line tool is optional, but useful for inspecting a development
  database. SQLite 3.38 or newer is required (the code uses `unixepoch()`);
  see [SQLite version](#sqlite-version).
- **Native tools:** Install `ffmpeg` and `ffprobe` for audio-related tests and
  development. The `pyacoustid` dependency also needs Chromaprint available on
  the system (commonly provided by a `chromaprint` or `libchromaprint` package).
- **Git:** Needed to check out the repository and prepare changes.

Some audio or transcription workflows may require additional resources, such
as model downloads or an LLM provider. The test suite is primarily local and
does not require live provider credentials.

## Set up the Python environment

From the repository root:

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install "torch==2.13.0" --index-url https://download.pytorch.org/whl/cpu
uv pip install -r requirements.txt
uv pip install ruff==0.15.20
```

CI installs CPU-only PyTorch separately from `requirements.txt`; the command
above mirrors that setup. If you do not use `uv`, create and activate a Python
3.12 virtual environment with your preferred tool, then run the equivalent
`python -m pip install` commands inside it.

### SQLite version

Python uses the SQLite library it was built against. Distributions such as
Ubuntu 22.04 ship SQLite 3.37.2, and `uv venv` reuses the system Python when
the version matches, so tests fail with
`sqlite3.OperationalError: no such function: unixepoch`. Check with:

```bash
python -c "import sqlite3; print(sqlite3.sqlite_version)"
```

If it prints a version older than 3.38, recreate the environment with a
uv-managed Python, which bundles a recent SQLite:

```bash
rm -rf .venv
uv venv --python 3.12 --managed-python
```

## Set up and build the frontend

From the repository root:

```bash
cd frontend
npm ci
npm run build
```

The production build is written to `static/ui/`, where Flask serves the web
interface.

While developing the frontend, these commands are also available:

```bash
npm run dev
npm run lint
npm run test
npm run test:browser
```

The Vite development server proxies API requests to the backend at
`localhost:8000`. See the [frontend development guide](../frontend/README.md)
for frontend structure and workflow details.

## Build and run the backend

The Python backend does not have a separate compilation step. Its source is
under `src/`; install the Python requirements above and set `PYTHONPATH=src`
when running backend commands from the repository root.

To build the application container from the repository root:

```bash
docker build -t minuspod:dev .
```

The Docker build includes the frontend build and packages the Python
application with its runtime dependencies. For container configuration and
running the service, see the [installation guide](installation.md).

## Run tests and checks

Run the complete Python test suite *after building the frontend* (some tests
exercise the built UI and favicon routes and will fail if `static/ui/` is
missing):

```bash
python -m pytest tests/ -v
```

> Note: `tests/conftest.py` adds `src/` to `sys.path`, so `PYTHONPATH=src`
is not needed for `pytest`.

Run a specific test file or directory by replacing `tests/` with its path, for
example:

```bash
python -m pytest tests/integration/test_root_redirect.py -v
```

Run the backend linter:

```bash
ruff check src tests
```

Run frontend checks from `frontend/`:

```bash
npm run lint
npm run test
npm run test:browser
npm run build
```

The browser tests require Playwright browsers to be installed. From
`frontend/`, run `npx playwright install chromium webkit` if they are not
already available.

CI also checks documentation links, dependency security, frontend design-guide
parity, and the backend's container import paths. Consult
[`ci.yml`](../.github/workflows/ci.yml) for the current CI commands.

## Pull requests

- Keep changes focused and include tests for behavior changes or bug fixes.
- Update relevant documentation when behavior, configuration, or workflows
  change.
- Run the relevant tests and checks above before opening a pull request.
- For changes that affect users, add an entry to `CHANGELOG.md` using the
  existing conventions.
- Follow the repository's pull request template and explain the problem,
  approach, and validation performed.

For release procedures and changelog conventions, see
[Releasing](releasing.md).
