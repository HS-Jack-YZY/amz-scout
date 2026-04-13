# Implementation Report: Phase 6 — Webapp Deployment

**Plan**: [phase6-deployment.plan.md](../plans/completed/phase6-deployment.plan.md)
**Source PRD**: [internal-amz-scout-web.prd.md](../prds/internal-amz-scout-web.prd.md) Phase 6
**Branch**: `feat/phase6-deployment`
**Dates**: planned 2026-04-13, executed 2026-04-13, rehearsal same-day
**Rehearsal instance**: `amz-scout-prod` @ `52.45.8.186` (us-east-1a, Lightsail, \$7 plan)

## Summary

Packaged the Chainlit webapp into a reproducible Docker image, fronted by a
Caddy reverse proxy, deployed to a fresh AWS Lightsail instance with a
persistent SQLite mount. Validated the full stack end-to-end via a real
golden-path query from an external browser. Zero application code changes.

## Assessment vs Reality

| Metric | Plan | Actual |
|---|---|---|
| Complexity | Medium | Medium — three unanticipated Dockerfile rebuilds, otherwise as planned |
| Confidence | n/a | Would have been high if I'd run `docker build` locally first |
| Files Changed | 8 new + 3 modified | 8 new + 3 modified + 3 docs patches post-rehearsal |
| Rehearsal time budget | ≤2 h | ~90 min (instance still live at report time pending Jack's decom) |
| Keepa token burn (rehearsal) | ~10 | 1 (golden path used a single ASIN passthrough) |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Add `httpx` to `[dev]` extras | done | |
| 2 | Create `.dockerignore` | done | |
| 3 | Create `Dockerfile` | done (3 iterations) | See "Dockerfile deviations" below |
| 4 | Create `docker-compose.yml` | done | |
| 5 | Create `deploy/Caddyfile` | done | |
| 6 | Create `deploy/first-time-setup.sh` | done | Required `BLOCK_DEVICE=/dev/nvme1n1` override at rehearsal time |
| 7 | Create `deploy/README.md` runbook | done | Patched with 5 new troubleshooting sections + rehearsal log after Task 12 |
| 8 | Create `scripts/smoke_deploy.sh` | done | |
| 9 | Create `tests/test_webapp_deployment_smoke.py` | done | Integration smoke, skipped by default |
| 10 | Update `.env.example` | done | |
| 11 | Update `CLAUDE.md` | done | |
| 12 | Lightsail rehearsal | done | Golden path validated via ASIN passthrough |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis (ruff check + format) | pass | 37 files clean |
| Unit Tests (`pytest -m unit`) | pass | 7 passed, 222 deselected |
| Deployment Smoke (skip-without-env) | pass | 2 tests skip cleanly without `AMZ_SCOUT_DEPLOY_URL` |
| Deployment Smoke (live HTTP probe) | pass | Ran against `http://52.45.8.186`, 3 steps all green |
| docker compose config | pass | Warnings about `$` interpolation flagged, harmless |
| docker build (on Lightsail) | pass after 3 iterations | See Dockerfile deviations |
| Container health (webapp + caddy Up) | pass | Both containers Up, no restart loops |
| External HTTP probe | pass | HTTP 200 from laptop, body contains Chainlit shell |
| Manual golden path (Jack) | pass | ASIN passthrough → LAZY Keepa fetch → DB write → rendered result |

## Files Changed

| File | Action | Commit(s) |
|---|---|---|
| `Dockerfile` | CREATE → MODIFY ×3 | `6f5c547` → `dc20ba6` → `f5fd634` → `78ff4c9` |
| `.dockerignore` | CREATE | `6f5c547` |
| `docker-compose.yml` | CREATE | `6f5c547` |
| `deploy/Caddyfile` | CREATE | `6f5c547` |
| `deploy/README.md` | CREATE → MODIFY | `6f5c547` → `cf6da3f` |
| `deploy/first-time-setup.sh` | CREATE | `6f5c547` |
| `scripts/smoke_deploy.sh` | CREATE | `6f5c547` |
| `tests/test_webapp_deployment_smoke.py` | CREATE | `6f5c547` |
| `pyproject.toml` | UPDATE (+httpx) | `6f5c547` |
| `.env.example` | UPDATE (+DOMAIN, +DEPLOY_EMAIL) | `6f5c547` |
| `CLAUDE.md` | UPDATE (Deployment commands) | `6f5c547` |
| `.claude/PRPs/prds/internal-amz-scout-web.prd.md` | UPDATE (Phase 6 complete) | `cf6da3f` |
| `.claude/PRPs/plans/phase6-deployment.plan.md` | ARCHIVED to `completed/` | `1dc9ab5` |

Commit chain on `feat/phase6-deployment`:
1. `6f5c547` — feat: Phase 6 infra scaffold (11 files)
2. `dc20ba6` — fix(docker): switch from uv tool install to pip install
3. `f5fd634` — fix(docker): use 'browser-use install' instead of 'playwright install'
4. `78ff4c9` — fix(docker): install uv alongside browser-use so 'browser-use install' works
5. `1dc9ab5` — docs: Phase 6 rehearsal lessons + mark phase complete (archive only)
6. `cf6da3f` — docs: capture Phase 6 rehearsal lessons in README troubleshooting + PRD

## Dockerfile Deviations (the only meaningful ones)

The plan specified layer A as:

```dockerfile
RUN pip install --no-cache-dir uv && uv tool install browser-use
ENV PATH="/root/.local/bin:${PATH}"
RUN playwright install chromium --with-deps
```

This failed three times in Lightsail rehearsal:

1. **First failure**: `uv tool install browser-use` isolates browser-use in a
   venv and only exposes the `browser-use` entry point on PATH, not
   `playwright`. Next layer could not find `playwright`.
2. **Second failure**: Replaced with `pip install browser-use`, assuming it
   would pull playwright as a transitive dep. browser-use 0.12+ replaced
   playwright with its own `cdp-use` client, so the transitive dep no longer
   exists. `playwright install` still failed with exit 127.
3. **Third failure**: Replaced with `browser-use install` (the modern
   upstream-recommended Chromium provisioner). This failed because
   browser-use's installer internally spawns `uvx` as a subprocess, and `uv`
   had been dropped after the second fix.

**Final working layer A**:

```dockerfile
RUN pip install --no-cache-dir uv browser-use
RUN browser-use install
```

Lesson recorded at:
- `~/.claude/projects/.../memory/feedback_dockerfile_upstream_introspection.md`
- `deploy/README.md` section 11 (three new troubleshooting subsections)

## Issues Encountered

1. **Lightsail account did not allow the \$12 plan** → downgraded to \$7
   (1 GB RAM, 2 vCPU). Mitigated with 2 GB swap file added before building.
   Build completed successfully with swap but ran close to memory limits
   during `browser-use install`.
2. **Block device path `/dev/xvdf` does not exist on Nitro NVMe instances**
   → actual path is `/dev/nvme1n1`. Bootstrap script was pre-designed to
   accept `BLOCK_DEVICE` env var override, so the fix was one env var.
3. **Static IP assignment changed the public IPv4**. First SSH attempt used
   the pre-binding dynamic IP `44.192.131.98` and timed out. Real IP was
   `52.45.8.186`. Documented in README troubleshooting.
4. **docker compose `$` interpolation warnings**. Jack's local `.env` contains
   a `$` substring inside one secret. Harmless in `env_file` mode (values
   pass through) but noisy. Fix is to escape `$` → `$$` in `.env`. Documented.
5. **perl locale warnings** during `apt-get install docker-ce`. Harmless
   (bookworm slim lacks full locale data). Documented so first-time operators
   don't panic.

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| `tests/test_webapp_deployment_smoke.py` | 2 integration tests | HTTP status + body assertion, skip-without-env contract |

Unit test suite unchanged: 7 existing webapp unit tests still pass, no
regressions.

## Golden Path Validation

Executed manually by Jack from an external browser:

1. Navigated to `http://52.45.8.186/`
2. Logged in with `@gl-inet.com` email + `APP_PASSWORD`
3. Asked about an Amazon product by ASIN
4. Observed:
   - LLM called tool (Anthropic API reachable from container)
   - tool routed to `amz_scout.api.query_trends`
   - 4-level resolution fell through to ASIN passthrough
   - LAZY Keepa fetch consumed 1 token (Keepa API reachable from container)
   - Result wrote to `/app/output/amz_scout.db` on the persistent mount
   - LLM rendered the time series back to the user

All end-to-end layers validated. PRD Phase 6 **Success signal** met.
PRD **Open Question Q2** (browser-use on headless Linux inside Lightsail
Docker) answered: **yes**, with the `pip install uv browser-use` +
`browser-use install` sequence.

## Follow-ups / Not This Phase

- **Decommission Lightsail instance**: Jack to `Delete` the instance + static
  IP + data disk via Lightsail console after he finishes reviewing the
  rehearsal. Instance costs ~\$0.02/hour; first 90 days are free but the
  instance should still not be left running indefinitely.
- **Open PR** for `feat/phase6-deployment` — awaiting Jack's authorization
  because PR creation is shared-state. Recommend merging into `main` after
  rehearsal teardown.
- **Domain + TLS**: Current HTTP-only mode (`DOMAIN=:80`) is the rehearsal
  configuration. When Jack obtains `amz-scout.<gl-inet-internal-host>` from
  IT, flip `.env` to `DOMAIN=<real>` + restart. Already documented.
- **Anthropic Zero Data Retention**: Tracked in
  `memory/project_web_deploy_zdr_todo.md`, target reminder ~2026-05-04.
- **Phase 3 (Management tools) + Phase 4 (long task UX)**: Rehearsal surfaced
  that without these phases, users cannot add new products or discover ASINs
  from within the webapp — only query pre-existing data or manually supply
  ASINs. This is PRD-expected and not a Phase 6 regression.

## Next Steps

- [ ] Jack: open PR `feat/phase6-deployment` → `main`
- [ ] Jack: decommission Lightsail rehearsal resources
- [ ] Jack: merge PR after any review comments
- [ ] Later: Phase 3 — Management tools
- [ ] Later: re-deploy with real domain + TLS once DNS is available
