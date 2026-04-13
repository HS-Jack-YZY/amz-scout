# Plan: Phase 6 — amz-scout Webapp Deployment

## Summary

Package the existing Chainlit webapp (`webapp/`, already working locally via `chainlit run webapp/app.py -w`) into a reproducible Docker image, deploy it to an AWS Lightsail 2GB/2vCPU instance fronted by a Caddy reverse proxy (automatic Let's Encrypt), mount the SQLite `output/` directory onto a persistent EBS block-storage volume, and verify the end-to-end production path via a smoke test. **Zero code changes** to `amz_scout/` or `webapp/` — Phase 6 is pure infra + runbook.

## User Story

As Jack (operator), I want a reproducible one-command deploy to a reachable HTTPS URL with whitelisted login, so that 小李 and other GL.iNet colleagues can hit `https://amz-scout.<gl-inet-internal-host>` from their browser without me running anything on my laptop.

## Problem → Solution

**Current**: The webapp runs only on Jack's laptop at `http://localhost:8000`. Colleagues cannot reach it. `browser-use` CLI + Chromium + Keepa + SQLite all work locally, but there's no image, no host, no TLS, no persistent volume, no restart-on-crash, no smoke test.

**Desired**: `docker compose up -d` on a Lightsail instance starts the webapp + Caddy edge. Users visit `https://amz-scout.<gl-inet-internal-host>`, log in with `@gl-inet.com` email + shared password, and run real queries. `output/amz_scout.db` persists across container restarts and daily Lightsail snapshots. A smoke test script proves the full path end-to-end.

## Metadata

- **Complexity**: Medium (no new application code; lots of infra surface area + one smoke test)
- **Source PRD**: `.claude/PRPs/prds/internal-amz-scout-web.prd.md`
- **PRD Phase**: Phase 6 — Deployment (W2 D5 – W3 D1, ~6h)
- **Depends on**: Phase 1 (scaffolding) — satisfied (merged in #3, #4)
- **Estimated files**: 8 new + 3 modified
- **Estimated Lines**: ~350 (mostly Dockerfile + compose + Caddyfile + runbook)
- **Answers Open Question**: Q2 (browser-use on headless Linux inside Lightsail Docker)

---

## UX Design

### Before (local-only dev loop)

```
┌────────────────────────────────────────────────┐
│  Jack's laptop only                            │
│  $ chainlit run webapp/app.py -w               │
│  ↓                                              │
│  http://localhost:8000 (loopback only)         │
│  ↓                                              │
│  (colleagues cannot reach it)                  │
│  ↓                                              │
│  Back to Slack "Jack 帮我查一下..."              │
└────────────────────────────────────────────────┘
```

### After (Phase 6 target)

```
┌──────────────────────────────────────────────────────┐
│  Any GL.iNet colleague, any browser, any network     │
│  ↓                                                    │
│  https://amz-scout.<gl-inet-internal-host>           │
│  ↓ (HTTPS, Caddy + Let's Encrypt auto-cert)          │
│  AWS Lightsail (2GB / 2vCPU, us-east-1)              │
│  ├─ Caddy container: :443 → :8000 reverse proxy      │
│  └─ webapp container: chainlit run webapp/app.py      │
│       ├─ browser-use CLI (headless Chromium)          │
│       ├─ /app/output (EBS mount) → amz_scout.db       │
│       └─ /app/.env (mount, chmod 600 on host)         │
│  ↓                                                    │
│  小李 types "show me latest UK data", gets reply      │
│  ↓                                                    │
│  docker compose restart survives container crash     │
│  Daily Lightsail snapshot survives disk loss         │
└──────────────────────────────────────────────────────┘
```

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| Reach | `localhost:8000` on Jack's laptop | Public HTTPS URL | DNS `amz-scout.<internal-host>` A-record → Lightsail static IP |
| TLS | None (HTTP) | Let's Encrypt via Caddy | Auto-renewal; no manual cert work |
| Auth | Unchanged | Unchanged | Same `@gl-inet.com` + shared `APP_PASSWORD` |
| DB persistence | Relative `output/amz_scout.db` on laptop | Mounted `/app/output/amz_scout.db` on EBS volume | Survives `docker compose down` and Lightsail snapshots |
| Restart policy | Manual re-run after crash | `restart: unless-stopped` in compose | Recovers from OOM, crashes, reboots |
| Process boundary | Bare Python on host | Python in `python:3.12-slim-bookworm` container | Reproducible across macOS/Linux |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `webapp/app.py` | 1-56 | Confirms entry point: `chainlit run webapp/app.py -w`. The Dockerfile's `CMD` must match this invocation minus `-w`. |
| P0 | `webapp/config.py` | 1-52 | Confirms absolute `DB_PATH = _REPO_ROOT / "output" / "amz_scout.db"` resolved at import time. The Docker mount must place a writeable `output/` directory at `/app/output/`. Enumerates required env vars. |
| P0 | `webapp/auth.py` | 1-42 | Password auth uses plaintext `APP_PASSWORD` — must be in the container's `.env`, `chmod 600` on host before mounting. |
| P0 | `pyproject.toml` | 1-59 | `[project.optional-dependencies].web` lists `chainlit>=2.7,<3`, `anthropic>=0.40`, `python-dotenv>=1.0`, `openpyxl>=3.1`. Dockerfile must `pip install -e ".[web]"`. Note `requires-python = ">=3.12"` → base image `python:3.12-slim-bookworm`. |
| P0 | `.env.example` | 1-13 | Enumerates required env: `KEEPA_API_KEY`, `ANTHROPIC_API_KEY`, `CHAINLIT_AUTH_SECRET`, `APP_PASSWORD`, `ALLOWED_EMAIL_DOMAIN`. Deployment runbook re-states these. |
| P0 | `src/amz_scout/browser.py` | 15-39 | Confirms `BrowserSession.__init__(headed=False)` default — `headed=False` is already the production default. **Deploy constraint**: never expose `headed=True` in webapp. |
| P1 | `src/amz_scout/db.py` | 113 | WAL mode is enabled → SQLite is concurrency-safe for 6 users. No change; deployment just needs a writeable volume. |
| P1 | `src/amz_scout/scraper/keepa.py` | 26-49 | `_load_dotenv()` walks up from `Path(__file__).parent.parent.parent.parent` → inside the container, the repo root must be `/app` so walking up still finds `/app/.env`. |
| P1 | `.chainlit/config.toml` | 1-40 | Chainlit session timeouts + `allow_origins = ["*"]`. Fine for internal MVP — no change needed. |
| P1 | `.gitignore` | 1-30 | `.env` and `output/` are both ignored → the runbook must explain how these land on the server (scp / rsync). |
| P2 | `tests/test_webapp_smoke.py` | 1-60 | Existing smoke-test pattern (env isolation + import integrity). The deploy smoke test mirrors this structure, adding one HTTP-level check. |
| P2 | `.claude/PRPs/plans/completed/phase1-webapp-scaffolding.plan.md` | 80-200 | Pattern source for "Patterns to Mirror" below; Phase 1 and Phase 2 plans are the canonical examples of this repo's plan style. |

---

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| Chainlit deployment | https://docs.chainlit.io/deploy/overview | Chainlit serves on port 8000 by default; `chainlit run app.py --host 0.0.0.0 --port 8000` is the production invocation. |
| Chainlit Docker guide | https://docs.chainlit.io/deploy/docker | Official Dockerfile reference: slim Python base + `pip install chainlit` + `CMD ["chainlit", "run", "app.py", "-h", "--host", "0.0.0.0", "--port", "8000"]` — `-h` is `--headless` (no browser auto-open). |
| `uv tool install browser-use` | https://docs.browser-use.com/quickstart | `uv tool install browser-use` installs into `~/.local/share/uv/tools/browser-use` and exposes `browser-use` on `$PATH` via `~/.local/bin`. In the Dockerfile this requires `ENV PATH="/root/.local/bin:${PATH}"`. |
| Playwright Chromium for Python | https://playwright.dev/python/docs/intro | `playwright install chromium --with-deps` pulls all OS libs; faster than hand-listing packages. browser-use uses Playwright under the hood. |
| Lightsail instance provisioning | https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-getting-started-with-docker.html | 2GB/2vCPU Linux blueprint is ~$12/month; supports Docker; static public IP is a separate resource (attach to instance so reboots don't change it). |
| Lightsail block storage | https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-creating-and-attaching-block-storage-disks.html | Create, attach, format (`mkfs.ext4`), mount at `/mnt/amz-scout-data` via `/etc/fstab`. |
| Lightsail snapshots | https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-creating-automatic-snapshots.html | Automatic daily snapshots are a built-in feature; enable on both the instance and the block-storage disk. |
| Caddy reverse proxy + Let's Encrypt | https://caddyserver.com/docs/automatic-https | `caddy` binary handles ACME + auto-renew; Caddyfile is 3 lines for a reverse-proxy + TLS host. Runs as a separate container in the same compose project. |
| Caddy Docker image | https://hub.docker.com/_/caddy | Official `caddy:2-alpine`; mount Caddyfile at `/etc/caddy/Caddyfile`; persist `/data` (certificates) and `/config`. |
| Anthropic Zero Data Retention | https://support.anthropic.com/en/articles/10440198 | ZDR enrollment takes ~1 week; post-MVP action (tracked in `memory/project_web_deploy_zdr_todo.md`). **Not** a Phase 6 blocker. |

---

## Research Findings

```
KEY_INSIGHT: Chainlit's production invocation differs from dev mode.
APPLIES_TO: Dockerfile CMD
GOTCHA: `-w` is watch mode (auto-reload on file change); do NOT use in production.
        Use `-h` (headless — don't try to open a browser on the host) and explicit
        `--host 0.0.0.0 --port 8000` so the container binds all interfaces.

KEY_INSIGHT: browser-use is installed via `uv tool install`, not pip.
APPLIES_TO: Dockerfile
GOTCHA: After `uv tool install browser-use`, the binary lives at
        `/root/.local/bin/browser-use` (running as root in the container). Must
        add this to PATH. Also, browser-use triggers `playwright install` on
        first use — run it at build time to avoid first-request latency.

KEY_INSIGHT: Chromium in a container needs specific OS libs.
APPLIES_TO: Dockerfile
GOTCHA: The cleanest path is `playwright install chromium --with-deps`,
        which auto-installs every required apt package. Skipping this leads
        to cryptic "chromium failed to launch: missing libxxx.so" errors at
        first browser-use call.

KEY_INSIGHT: Chainlit writes session state under `.chainlit/` by default.
APPLIES_TO: docker-compose.yml volumes
GOTCHA: The Chainlit session timeout is 15 days (.chainlit/config.toml:9).
        If the container is ephemeral and `.chainlit/` session state is not
        mounted, users are force-logged-out on every deploy. For MVP this is
        acceptable (6 users, rare deploys), but note it in the runbook.

KEY_INSIGHT: Lightsail static IP is a separate resource that must be attached.
APPLIES_TO: Provisioning runbook
GOTCHA: If you reboot or upgrade the instance without a static IP attached,
        the public IP changes and DNS breaks. Attach BEFORE pointing DNS.

KEY_INSIGHT: SQLite WAL files must stay on the same filesystem as the DB.
APPLIES_TO: docker-compose.yml volume mount
GOTCHA: Do NOT bind-mount just `amz_scout.db` — mount the entire `output/`
        directory so `amz_scout.db-wal` and `amz_scout.db-shm` land alongside
        it. Mounting only the single file can corrupt WAL on container restart.

KEY_INSIGHT: The webapp uses an absolute DB path resolved at import time.
APPLIES_TO: Dockerfile WORKDIR
GOTCHA: webapp/config.py:37 computes DB_PATH = _REPO_ROOT / "output" / "amz_scout.db"
        where _REPO_ROOT = Path(__file__).parent.parent. So inside the container,
        the project must live at a stable path (use `/app`), and `/app/output/`
        must be the mount point. No env override exists — do not introduce one
        in this phase; match the existing convention.
```

---

## Patterns to Mirror

### ENVELOPE_PATTERN (applies to any new test assertion)
```python
# SOURCE: src/amz_scout/api.py:287-302
def _envelope(ok, data=None, error=None, **meta):
    return {"ok": ok, "data": data if data is not None else [], "error": error, "meta": meta}
```
**Deployment usage**: The smoke test asserts envelope shape without mutating it, mirroring `tests/test_webapp_smoke.py`.

### LOGGER_PATTERN (for any new Python)
```python
# SOURCE: webapp/app.py:22-24
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)
```
**Deployment usage**: Any new Python file uses `logger = logging.getLogger(__name__)` and never `print()` (per `rules/python/hooks.md`).

### ENV_LOADING_PATTERN
```python
# SOURCE: webapp/config.py:11-18
_REPO_ROOT = Path(__file__).parent.parent
_ENV_FILE = _REPO_ROOT / ".env"
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE)
```
**Deployment usage**: Inside the container, repo root = `/app`, so `.env` must be mounted at `/app/.env`. The runbook states this explicitly. No code change.

### TEST_STRUCTURE (for the deploy smoke test)
```python
# SOURCE: tests/test_webapp_smoke.py:28-50
@pytest.mark.unit
class TestWebappImports:
    def test_config_imports(self, monkeypatch):
        _set_fake_env(monkeypatch)
        _reset_webapp_modules()
        from webapp import config
        config.validate_env()
```
**Deployment usage**: The deploy smoke test lives at `tests/test_webapp_deployment_smoke.py`, uses `@pytest.mark.integration`, and targets an external URL via `httpx` (not mocks).

### LINT + FORMAT (unchanged)
```
# SOURCE: pyproject.toml:44-49
[tool.ruff]
target-version = "py312"
line-length = 100
[tool.ruff.lint]
select = ["E", "F", "I", "W"]
```
**Deployment usage**: Any new Python must pass `ruff check` and `ruff format` identical to the rest of the repo.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `Dockerfile` | **CREATE** | Builds the webapp image. |
| `.dockerignore` | **CREATE** | Keeps `output/`, `.git/`, `__pycache__/`, `.venv/`, `.env` out of the build context. |
| `docker-compose.yml` | **CREATE** | Two services: `webapp` (Chainlit) and `caddy` (TLS reverse proxy). Defines volumes, network, restart policy, env file. |
| `deploy/Caddyfile` | **CREATE** | 5-line reverse proxy config: `{$DOMAIN} { reverse_proxy webapp:8000 }` |
| `deploy/README.md` | **CREATE** | One-time provisioning runbook + repeatable deploy/update procedure. |
| `deploy/first-time-setup.sh` | **CREATE** | Idempotent host bootstrap: install Docker, create `/mnt/amz-scout-data` mount, set `.env` perms. |
| `scripts/smoke_deploy.sh` | **CREATE** | 3-step HTTP smoke test; invoked from the runbook's final step. |
| `tests/test_webapp_deployment_smoke.py` | **CREATE** | `@pytest.mark.integration` — live HTTP probe using `httpx`. Skipped by default. |
| `.env.example` | **UPDATE** | Add `DOMAIN=` and `DEPLOY_EMAIL=` placeholders. |
| `CLAUDE.md` | **UPDATE** | Add "Deployment" subsection with the 3 primary commands. |
| `pyproject.toml` | **UPDATE** | Add `httpx` to the `dev` optional-dependencies group. |

## NOT Building

- **AWS Secrets Manager / Parameter Store integration** — `.env` on EBS with `chmod 600` is MVP-sufficient (PRD §Technology Decisions).
- **GitHub Actions CI/CD auto-deploy** — manual `git pull && docker compose up -d --build` is fine for 6 users, ~1 deploy/week.
- **Anthropic Zero Data Retention enrollment** — explicitly deferred in PRD.
- **Multi-region / multi-AZ / HA** — daily snapshot + 5-minute manual recovery is the RTO budget.
- **Per-user bcrypt auth** — PRD keeps MVP on shared `APP_PASSWORD`.
- **Prometheus/Grafana monitoring** — `docker compose logs` + weekly silent-failure check is enough.
- **Database backups beyond Lightsail snapshots** — Lightsail daily snapshots cover instance + block-storage disk.
- **Mounting `.chainlit/` session state** — accept forced re-login on rare deploys.
- **Any changes to `amz_scout/` or `webapp/` Python code** — Phase 6 is pure infra. If a code change looks necessary, STOP and escalate.

---

## Step-by-Step Tasks

### Task 1: Add `httpx` to `[dev]` extras in `pyproject.toml`
- **ACTION**: Add `"httpx>=0.27"` to `[project.optional-dependencies].dev`.
- **IMPLEMENT**: One-line addition.
- **MIRROR**: Existing `dev` block at `pyproject.toml:18-23`.
- **GOTCHA**: Do NOT add to `dependencies` — smoke test is dev-only. Do NOT add to `web` — it's a test client, not runtime.
- **VALIDATE**: `pip install -e ".[dev]"` succeeds; `python -c "import httpx"` works.

### Task 2: Create `.dockerignore`
- **ACTION**: Enumerate patterns to exclude from Docker build context.
- **IMPLEMENT**: Match `.gitignore` plus Docker-specific additions.
- **MIRROR**: `.gitignore` lines 1-30.
- **GOTCHA**: MUST include `.env` (never let secrets enter the image layer) and `output/` (database must live in the mount, not the image). Also exclude `.git/`, `__pycache__/`, `.venv/`, `.pytest_cache/`, `.ruff_cache/`, `htmlcov/`, `dist/`, `build/`, `*.egg-info/`.
- **VALIDATE**: `docker build .` finishes with a build-context size under ~5MB.

### Task 3: Create `Dockerfile`
- **ACTION**: Single-stage Dockerfile with 3 cache-friendly layer groups (system → python deps → static assets).
- **IMPLEMENT**: Order matters — Phase 7 Alpha will iterate on `webapp/tools.py` frequently, so put the slowest layers first so they stay cached.
  ```dockerfile
  # ── Layer group A: system deps + browser-use + Chromium (slow, ~8 min, rarely changes) ──
  FROM python:3.12-slim-bookworm
  RUN apt-get update \
      && apt-get install -y --no-install-recommends curl ca-certificates git \
      && rm -rf /var/lib/apt/lists/*
  RUN pip install --no-cache-dir uv \
      && uv tool install browser-use
  ENV PATH="/root/.local/bin:${PATH}"
  RUN playwright install chromium --with-deps

  # ── Layer group B: Python dependencies (medium, ~1 min, triggered by pyproject.toml change) ──
  WORKDIR /app
  COPY pyproject.toml README.md ./
  COPY src/ src/
  COPY webapp/ webapp/
  RUN pip install --no-cache-dir -e ".[web]"

  # ── Layer group C: static assets (fast, seconds, triggered by config/.chainlit change) ──
  COPY config/ config/
  COPY .chainlit/ .chainlit/
  COPY chainlit.md ./

  EXPOSE 8000
  CMD ["chainlit", "run", "webapp/app.py", "-h", "--host", "0.0.0.0", "--port", "8000"]
  ```
- **MIRROR**: Chainlit's official Docker deploy guide; layer ordering mirrors `pyproject.toml:34-35` package layout.
- **GOTCHA**:
  - `-h` is `--headless` — NOT `-w` (watch). Watch mode must not ship.
  - `config/` directory must be copied — `config/marketplaces.yaml` is required at runtime.
  - Do NOT `COPY .env` or `COPY output/`.
  - `uv tool install browser-use` MUST run before `playwright install chromium --with-deps` — browser-use pins a specific Playwright revision; reverse order silently installs a mismatched Chromium that fails at first browser launch with a cryptic error.
  - Keep `COPY src/ src/` and `COPY webapp/ webapp/` in layer B (not C) because `pip install -e ".[web]"` needs the package source already present.
- **VALIDATE**:
  - `docker build -t amz-scout-web:dev .` succeeds.
  - `docker run --rm amz-scout-web:dev which browser-use` prints `/root/.local/bin/browser-use`.
  - `docker run --rm amz-scout-web:dev chainlit --version` prints ≥ 2.7.
  - **DB auto-create smoke**: `docker run --rm -v $(mktemp -d):/app/output amz-scout-web:dev python -c "from amz_scout.db import open_db, resolve_db_path; conn = open_db(resolve_db_path()); print('db-ok')"` prints `db-ok`. This verifies `src/amz_scout/db.py` creates all tables from scratch when the mount is empty — otherwise the first boot on Lightsail would fail.

### Task 4: Create `docker-compose.yml`
- **ACTION**: Two services (`webapp`, `caddy`), one named network, bind mounts, env_file.
- **IMPLEMENT**:
  - `services.webapp`: `build: .`, `env_file: ./.env`, volumes: `./output:/app/output` (RW) and `./.env:/app/.env:ro`, `expose: [8000]`, `restart: unless-stopped`, `networks: [app]`
  - `services.caddy`: `image: caddy:2-alpine`, volumes: `./deploy/Caddyfile:/etc/caddy/Caddyfile:ro`, `caddy_data:/data`, `caddy_config:/config`, ports: `80:80`, `443:443`, `environment: [DOMAIN, DEPLOY_EMAIL]`, `restart: unless-stopped`, `networks: [app]`, `depends_on: [webapp]`
  - `networks.app: {}`
  - `volumes: {caddy_data: {}, caddy_config: {}}`
- **GOTCHA**:
  - Mount `./output` read-write (SQLite WAL).
  - Mount `.env` read-only into the container.
  - `expose: [8000]` (intra-compose only); do NOT publish 8000 on the host. Only Caddy publishes 80/443.
  - Use `caddy_data` named volume (NOT bind mount) so Caddy manages permissions.
  - `depends_on` only affects start order, not readiness — fine for MVP.
- **VALIDATE**: `DOMAIN=localhost docker compose config` prints a valid parsed config.

### Task 5: Create `deploy/Caddyfile`
- **ACTION**: Minimal Caddy v2 config with env-var domain.
- **IMPLEMENT**:
  ```
  {$DOMAIN}

  encode zstd gzip
  reverse_proxy webapp:8000
  ```
- **GOTCHA**: `{$DOMAIN}` is Caddy's env substitution syntax. Caddy handles HTTPS redirect + Let's Encrypt automatically when a non-localhost domain is provided.
- **VALIDATE**: `docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile` returns OK.

### Task 6: Create `deploy/first-time-setup.sh`
- **ACTION**: Idempotent bash host bootstrap.
- **IMPLEMENT**: Install Docker Engine + Compose plugin, add `ubuntu` user to `docker` group, format (only if blank) + mount the attached block-storage disk at `/mnt/amz-scout-data`, add `/etc/fstab` entry, `mkdir -p /mnt/amz-scout-data/output` with `chmod 755`, symlink `/mnt/amz-scout-data/output` → `$REPO/output` after repo clone, set `.env` perms to `600`. All steps guarded with existence checks so the script is safe to rerun.
- **GOTCHA**:
  - `mkfs.ext4` MUST only run if the disk has no filesystem (`blkid $DEVICE | grep -q TYPE` guard). Otherwise rerunning nukes data.
  - Add `/etc/fstab` entry (with `nofail` option) so the mount survives reboot but doesn't block boot if the disk detaches.
  - `mkdir -p /mnt/amz-scout-data/output && chmod 755 /mnt/amz-scout-data/output` — webapp container runs as root, so perm 755 (or 777 if uid-sharing issues arise) is required for SQLite WAL writes. Symptom if skipped: `sqlite3.OperationalError: attempt to write a readonly database` after first boot.
  - Do NOT copy secrets into the script; runbook instructs Jack to `scp .env` after the script runs.
  - Script must be executable (`chmod +x deploy/first-time-setup.sh`).
- **VALIDATE**: Running twice on a fresh Lightsail instance leaves the host in the same state both times. `docker --version` and `docker compose version` both work without `sudo`. `mount | grep amz-scout-data` shows the mount. `ls -ld /mnt/amz-scout-data/output` shows `drwxr-xr-x` or broader.

### Task 7: Create `deploy/README.md` (provisioning + deploy runbook)
- **ACTION**: Step-by-step runbook.
- **IMPLEMENT**: Sections:
  1. Prerequisites (AWS Lightsail access, domain controlled, Anthropic + Keepa keys)
  2. Provision Lightsail instance (Ubuntu 22.04, 2GB/2vCPU)
  2a. Attach static public IP to the instance (BEFORE pointing DNS)
  2b. **Open firewall ports**: Lightsail console → Instance → Networking → IPv4 Firewall → add rules for `HTTP (TCP 80, Source: Any IPv4)` and `HTTPS (TCP 443, Source: Any IPv4)`. **Do NOT skip** — Lightsail's default firewall only allows 22/tcp, so Caddy will fail ACME and reverse-proxy will be unreachable until this is done.
  2c. Point DNS A-record at the static IP; verify with `dig +short $DOMAIN`.
  3. Attach block storage (20GB disk, note device path, usually `/dev/xvdf`)
  4. Bootstrap the host (SSH in, clone repo, run `deploy/first-time-setup.sh`)
  5. Install secrets (`scp .env ubuntu@host:amz-scout/.env`, `chmod 600 .env`, set `DOMAIN=` and `DEPLOY_EMAIL=`)
  6. First deploy (`docker compose up -d --build`; tail logs with `docker compose logs -f`)
  7. Smoke test (`scripts/smoke_deploy.sh $DOMAIN`)
  8. Subsequent updates (`git pull && docker compose up -d --build`)
  9. Backup / restore (enable Lightsail automatic daily snapshots on BOTH the instance AND the block-storage disk; manual restore = attach snapshot → swap mount)
  10. Rollback (`git checkout <prev-tag> && docker compose up -d --build`)
  11. **Troubleshooting** — include at minimum:
      - "browser-use missing Chromium" → rebuild image (Dockerfile problem, not host problem)
      - "401 on login" → check `.env`'s `APP_PASSWORD` and `ALLOWED_EMAIL_DOMAIN`
      - "TLS fails / Caddy stuck at ACME" → check ports 80/443 are open in Lightsail firewall (Step 2b), check DNS has propagated (`dig +short $DOMAIN`), check Caddy logs for rate-limit errors
      - "Users force-logged-out after every deploy" → **`CHAINLIT_AUTH_SECRET` must NEVER be regenerated** after first boot; it signs session JWTs, so rotating it invalidates every active session. Treat it like a database schema: set once, leave alone. Only regenerate if you intentionally want to kick every user out.
      - "Can't write to DB / readonly database" → check `output/` directory ownership; container runs as root, so bind-mount target on host should be root-owned (or permissive)
      - "502 Bad Gateway from Caddy" → webapp container crashed; check `docker compose logs webapp`
- **MIRROR**: `CLAUDE.md`'s "Commands" section style — dense, copy-pasteable.
- **GOTCHA**: Every command must be copy-pasteable; every path must be absolute. Acid test: a colleague with SSH access and this README should re-provision from scratch in ≤1h without pinging Jack.
- **VALIDATE**: Task 12 rehearsal proves it.

### Task 8: Create `scripts/smoke_deploy.sh`
- **ACTION**: Bash wrapper running HTTP-level smoke tests.
- **IMPLEMENT**:
  - `set -euo pipefail`
  - `$1` or `$AMZ_SCOUT_DEPLOY_URL` as target URL.
  - Step 1: `curl -fsS -o /dev/null -w "%{http_code}" "$URL/"` → expect `200` or `302`.
  - Step 2: `curl -fsS "$URL/" | grep -q "Chainlit"` → login page served.
  - Step 3: `AMZ_SCOUT_DEPLOY_URL="$URL" pytest tests/test_webapp_deployment_smoke.py -v -m integration`.
- **GOTCHA**: macOS bash is 3.2; avoid bash-4 syntax. Quote `"$URL"`. Do NOT depend on `jq`.
- **VALIDATE**: `bash -n scripts/smoke_deploy.sh` passes. Running against a local `chainlit run` prints all 3 ✓.

### Task 9: Create `tests/test_webapp_deployment_smoke.py`
- **ACTION**: `@pytest.mark.integration` test using `httpx` to probe a deployed instance.
- **IMPLEMENT**:
  - `pytest.importorskip("httpx")` at module top
  - `class TestDeployment`
  - `test_skipped_without_env` — auto-skip unless `AMZ_SCOUT_DEPLOY_URL` is set
  - `test_login_page_reachable` — `httpx.get(f"{url}/", follow_redirects=True, timeout=10)` returns 200 and body contains `"Chainlit"` or `"amz-scout"`. **Checking for the literal string `"Chainlit"` (not just status 200)** is important because Caddy serves its own default 404/welcome page when the upstream is down — a plain `200` assertion would pass against a broken deploy.
  - Do NOT attempt to log in (plaintext password in test config would leak); login correctness is verified manually.
- **MIRROR**: `tests/test_webapp_smoke.py` class structure.
- **GOTCHA**: MUST use `@pytest.mark.integration`. MUST skip cleanly when env var is unset. MUST NOT touch websockets — HTTP only.
- **VALIDATE**: `pytest tests/test_webapp_deployment_smoke.py` → skipped. With `AMZ_SCOUT_DEPLOY_URL=http://localhost:8000` and a running chainlit → passed.

### Task 10: Update `.env.example`
- **ACTION**: Append two new deployment variables.
- **IMPLEMENT**:
  ```
  # ─── Deployment (Phase 6) ────────────────────────────────────────
  # Public domain the Caddy edge serves; matches DNS A-record
  DOMAIN=amz-scout.example.internal
  # Contact email Caddy registers with Let's Encrypt for renewal alerts
  DEPLOY_EMAIL=ops@gl-inet.com
  ```
- **MIRROR**: Existing `.env.example:4-12` section-header style.
- **GOTCHA**: Do NOT put a real domain in the example file.
- **VALIDATE**: `DOMAIN=... docker compose config` succeeds with the new vars substituted.

### Task 11: Update `CLAUDE.md`
- **ACTION**: Add "Deployment" subsection under `## Commands`.
- **IMPLEMENT**:
  ```
  # ── Deployment (Phase 6, production) ──
  docker compose up -d --build          # Build + start webapp + Caddy edge
  docker compose logs -f webapp         # Tail webapp logs
  docker compose logs -f caddy          # Tail TLS / ACME logs
  scripts/smoke_deploy.sh $DOMAIN       # End-to-end deploy smoke test
  # Full runbook: deploy/README.md
  ```
- **MIRROR**: Existing `### Commands` block in `CLAUDE.md`.
- **GOTCHA**: Keep it short — this is a quick-reference. Point to `deploy/README.md` for anything procedural.
- **VALIDATE**: Markdown still renders; section sits cleanly under existing Commands.

### Task 12: End-to-end rehearsal on a disposable Lightsail instance
- **ACTION**: Stand up a real Lightsail instance, run the full runbook, verify golden-path query, tear down within 2 hours.
- **IMPLEMENT**: Follow `deploy/README.md` steps 1–7 exactly. Record any drift between doc and reality. Fix the doc (not the process) if the doc is wrong.
- **MIRROR**: PRD User Flow Golden Path.
- **GOTCHA**:
  - Not optional — answers PRD **Open Question Q2**.
  - Use a **disposable, short-lived** instance named `amz-scout-rehearsal-YYYYMMDD`. **Target lifetime: ≤2 hours** (Lightsail 2GB instance costs ~$0.017/hour, so a full rehearsal ≈ $0.03). Tear down via Lightsail console immediately after validation — do NOT leave it running overnight.
  - Use a **separate test subdomain** (e.g. `amz-scout-test.<gl-inet-internal-host>`) so the production DNS record is untouched if the rehearsal goes sideways.
  - Use Caddy's **ACME staging endpoint** during rehearsal (add `acme_ca https://acme-staging-v02.api.letsencrypt.org/directory` to the Caddyfile temporarily) to avoid burning Let's Encrypt production rate limits (5 certs/week/domain).
  - First `query_trends` against a fresh DB will fetch live Keepa data. Budget ~10 tokens.
  - If browser-use fails, Task 3 (Dockerfile) is wrong — fix the Dockerfile, rebuild the image, redeploy. **Do NOT patch on the host** — that breaks reproducibility for the real production instance.
  - Record the rehearsal outcome in `deploy/README.md` under "Troubleshooting" as a dated note so future-Jack (and 小李) have an incident-log of what actually happened on AWS headless Linux.
- **VALIDATE**: All smoke-test steps pass; manual "show me latest UK data" returns a real envelope; `docker compose logs` shows no unhandled exceptions for 10 minutes of idle time; instance is torn down within 2 hours.

---

## Testing Strategy

### HTTP smoke tests (new)

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_skipped_without_env` | no `AMZ_SCOUT_DEPLOY_URL` | test skipped | ✓ (dev default) |
| `test_login_page_reachable` | `GET $URL/` | 200, body contains `"Chainlit"` | ✓ (TLS ok) |

### Manual verification (covered by Task 12)
- [ ] HTTPS cert issues automatically on first boot
- [ ] Login with `@gl-inet.com` + `APP_PASSWORD` succeeds
- [ ] Login with `@external.com` is rejected
- [ ] `query_latest(marketplace="UK")` round-trips and renders
- [ ] `query_trends(product="Slate 7", marketplace="UK")` auto-fetches live Keepa data and renders
- [ ] `docker compose restart webapp` does not lose the DB (`SELECT count(*) FROM products` before+after matches)
- [ ] `docker compose down && docker compose up -d` preserves the DB

### Edge cases checklist
- [ ] Empty `/app/output/` on first boot (DB auto-created by `amz_scout.db`)
- [ ] Missing `.env` (container exits cleanly with the "Missing required environment variables" error from `webapp/config.py:51`)
- [ ] Wrong `APP_PASSWORD` (auth returns 401, logs the rejection)
- [ ] browser-use Chromium crash (first deploy after image rebuild — Task 12 catches this)
- [ ] Network partition to Keepa API during a query (envelope returns `ok=False`, surfaced to user)
- [ ] Caddy ACME rate limit (Let's Encrypt 5/week on same domain) — documented in troubleshooting

---

## Validation Commands

### Static Analysis
```bash
ruff check webapp/ tests/ scripts/ deploy/
ruff format --check webapp/ tests/
```
EXPECT: Zero errors.

### Unit Tests
```bash
pytest -m unit
```
EXPECT: All existing unit tests still pass (Phase 6 adds no new unit tests).

### Compose Config Parse
```bash
DOMAIN=localhost docker compose config > /dev/null
```
EXPECT: No parse errors.

### Caddy Config Validate
```bash
docker run --rm -v ./deploy/Caddyfile:/etc/caddy/Caddyfile:ro caddy:2-alpine \
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```
EXPECT: `Valid configuration`.

### Docker Build
```bash
docker build -t amz-scout-web:dev .
docker run --rm amz-scout-web:dev which browser-use
docker run --rm amz-scout-web:dev chainlit --version
docker run --rm amz-scout-web:dev python -c "from webapp import config; print(config.MODEL_ID)"
```
EXPECT: Build succeeds in <10 min (Playwright Chromium download dominates); all probes print expected values.

### Local Compose Smoke
```bash
cp .env .env.compose-smoke
echo "DOMAIN=localhost" >> .env.compose-smoke
docker compose --env-file .env.compose-smoke up -d --build
sleep 5
curl -fsS http://localhost/
docker compose down
```
EXPECT: 200 response. Confirms image + compose wiring before touching real infra.

### Deployment Smoke (after Task 12)
```bash
scripts/smoke_deploy.sh https://amz-scout.<gl-inet-internal-host>
```
EXPECT: All 3 steps ✓; exits 0.

### Full Test Suite
```bash
pytest
```
EXPECT: No regressions; integration tests skipped when `AMZ_SCOUT_DEPLOY_URL` unset.

### Manual Validation (after Task 12)
- [ ] Log in from a fresh incognito browser at the public HTTPS URL
- [ ] "show me latest UK data" → tool call executes, envelope rendered
- [ ] "GL-Slate 7 在英国过去 7 天价格" → `query_trends` runs, data returned
- [ ] Refresh after idle minute → session persists
- [ ] `docker compose restart webapp` → reload UI → DB rows still visible
- [ ] SSH `htop` → webapp container idle RSS < 700MB

---

## Acceptance Criteria

- [ ] All 12 tasks completed
- [ ] All validation commands pass
- [ ] `deploy/README.md` is self-sufficient (Task 12 rehearsal proves it)
- [ ] Jack completes one real query end-to-end from a browser on the public HTTPS URL (PRD Phase 6 "Success signal")
- [ ] PRD **Open Question Q2** answered in writing
- [ ] `@gl-inet.com` whitelist enforced on the deployed instance (verified by attempting login with an external-domain email)
- [ ] SQLite DB persists across `docker compose down && up`
- [ ] No `CRITICAL` or `HIGH` code-review findings
- [ ] Post-deploy: update PRD Phase 6 status from `pending` → `complete` and link this plan in the PRP table

---

## Completion Checklist

- [ ] Dockerfile builds reproducibly and mirrors `pyproject.toml`'s `[web]` extras
- [ ] Compose file mounts `output/` RW and `.env` RO; never publishes 8000
- [ ] Caddyfile uses `{$DOMAIN}` env substitution; no hardcoded host
- [ ] First-time-setup script is idempotent
- [ ] Runbook has every copy-pasteable command Jack needs in an outage
- [ ] Integration smoke test skips cleanly when env var is missing
- [ ] No `print()` statements in new Python (use `logging`)
- [ ] Line length ≤100 (ruff) in all new Python
- [ ] No secrets in any tracked file — `.env.example` has placeholders only
- [ ] `CLAUDE.md` updated with the quick-reference
- [ ] Phase 6 rehearsal on a disposable instance completed

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| browser-use fails to launch headless Chromium inside the container | **Medium** | Medium | Task 12 rehearsal catches this. Fallback: switch from `playwright install chromium --with-deps` to hand-listed apt deps + pinned Chromium. |
| Let's Encrypt rate-limits on the chosen domain during iteration | Low-Medium | Low | Use Caddy ACME **staging** endpoint during Task 12 rehearsal; switch to production endpoint only for the final prod deploy. Production rate limit: 5 certs/week/domain. |
| Lightsail 2GB too small under concurrent load | Low | Medium | Chainlit + Chromium is ~500-800MB RSS idle. If Task 12 shows >1.5GB, upgrade to 4GB plan (~$20/month, still inside internal-tool budget). |
| SQLite WAL corruption from bind-mount race | Low | High | Mount the `output/` directory, not individual files (Task 4 gotcha). WAL mode is designed for this pattern. |
| `.env` accidentally committed | Low | Critical | `.dockerignore` + `.gitignore` + pre-existing pre-commit checks. |
| DNS cut-over delay breaks Task 12 rehearsal | Low | Low | Use `curl --resolve` as a manual override while DNS propagates; runbook notes this. |
| Chainlit version drift breaks auth | Low | Medium | `pyproject.toml` pins `chainlit>=2.7,<3`. Rebuild locks the version into the image layer. |
| Anthropic API key leaked in `docker compose logs` | Low | High | Anthropic SDK does not log the key; Chainlit does not log env vars. Verify during Task 12 by `docker compose logs webapp \| grep -c sk-ant-` returns 0. |
| **Lightsail firewall blocks 80/443 by default** | **Medium** | **High** | Default Lightsail Ubuntu blueprint only opens 22/tcp. Runbook Step 2b explicitly instructs opening HTTP+HTTPS. Symptom if missed: Caddy stuck at ACME challenge, unreachable from browser. |
| **`CHAINLIT_AUTH_SECRET` rotated on redeploy** | Low | High | Every user force-logged-out. Runbook Step 5 + Task 7 troubleshooting entry: set once, never rotate. Pattern: treat like DB schema — append, don't mutate. |
| **Container writes to bind-mounted `output/` fail with EACCES** | Low-Medium | High | Container runs as root; host-side `/mnt/amz-scout-data/output/` must be writeable. `deploy/first-time-setup.sh` sets `chmod 755` on the mount point. Symptom if broken: `sqlite3.OperationalError: attempt to write a readonly database`. |

---

## Notes

- **Why Caddy, not nginx**: 5-line config, automatic ACME, zero manual cert work. nginx-proxy + acme-companion doubles the container count. Internal tool → simpler wins.
- **Why Lightsail, not EC2/ECS**: Locked-in by the PRD Decisions Log. 6 users, no autoscaling needed.
- **Why a single-stage Dockerfile**: Multi-stage saves ~400MB but Phase 6 optimizes for **debuggability over image size**. Size is a v1.1 candidate.
- **Why the smoke test stops at HTTP 200**: A tool-round-trip test needs a test account and Keepa token burn. HTTP + manual round-trip is the right MVP trade.
- **Deferred items tracked elsewhere**:
  - Anthropic ZDR: `memory/project_web_deploy_zdr_todo.md`, target reminder ~2026-05-04
  - Per-user auth: Phase 8+ follow-up
  - Secrets Manager: v1.1 optional
  - CI/CD auto-deploy: v1.1 optional
- **Post-Phase 6 handoff to Phase 7 (Alpha)**: Once this plan ships, Jack can invite 小李. Phase 7 runs against the same production URL — no infra changes.
