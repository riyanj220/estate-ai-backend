---
title: 'Story 1.4: Security baseline'
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'aed62f27303c2735d2e5c67f959f30a3988e9588'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-estate-ai-backend-2026-11-19/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-3-api-contract-error-envelope-and-structured-logging.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Hosts, origins, cookies, response headers, absolute links and client addresses have no safe defaults, so later stories could ship host-header, CSRF, clickjacking or address-spoofing gaps.

**Approach:** Configure secure settings once (hosts, trusted origins, cookie flags, HSTS), add a security-headers middleware with a Content-Security-Policy, a `FRONTEND_BASE_URL` link builder, and a trusted-proxy client-address resolver, each enforced by tests, and prove the Django admin is absent.

## Boundaries & Constraints

**Always:** hosts, trusted origins, trusted proxies and `FRONTEND_BASE_URL` come from environment variables; no wildcard host or origin is ever accepted; prod refuses to start on a missing or invalid value, naming the variable and never a value; session and CSRF cookies are HttpOnly and SameSite=Lax, and Secure outside local and test; headers apply to every response including errors; absolute links never use the Host header; forwarded address headers are trusted only from configured proxies, and the empty default trusts none; import-linter contracts keep passing; comments only where intent is not obvious.

**Never:** add authentication, sessions apps, models or migrations; install the Django admin; trust `X-Forwarded-*` from an unconfigured source; change the error envelope or log format from Story 1.3; build rate limiting (Story 1.7 will reuse the client-address resolver).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Bad hosts | prod with `DJANGO_ALLOWED_HOSTS` empty, unset or containing `*` | startup fails | `ImproperlyConfigured` names the variable |
| Bad origins | `CSRF_TRUSTED_ORIGINS` entry is `*` or lacks a scheme | startup fails | same |
| Bad frontend URL | prod `FRONTEND_BASE_URL` unset, not `https`, or has a path | startup fails | same |
| Cookies | session and CSRF cookie settings | HttpOnly, SameSite=Lax; Secure in prod, not in local or test | N/A |
| Headers | any response (200, 404, 500) | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Cross-Origin-Opener-Policy`, `Permissions-Policy` and a restrictive `Content-Security-Policy` present | N/A |
| HSTS | prod settings | `SECURE_HSTS_SECONDS` set with subdomains; absent in local and test | N/A |
| Host header attack | request with `Host: evil.example` | 400 envelope | no body echo of the host |
| Link building | `frontend_url("/verify")` with a hostile Host header present | `FRONTEND_BASE_URL` + path | a path with a scheme, `//` or backslash is rejected |
| Admin | enumerate routes under test and prod settings | no `admin` route; admin app not installed | N/A |
| Forwarded headers | `X-Forwarded-For` from an untrusted source | ignored; `REMOTE_ADDR` is the client | N/A |
| Trusted proxy | request from a configured proxy with a forwarded chain | client is the right-most address not in the trusted list | invalid or `/0` trusted entry fails startup |

</frozen-after-approval>

## Code Map

- `config/env.py` -- add `env_list` and shared validators for hosts, origins and CIDR lists
- `config/settings/{base,local,test,prod}.py` -- `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, cookie flags, HSTS (prod), `FRONTEND_BASE_URL`, `TRUSTED_PROXIES`; local defaults `127.0.0.1`, `localhost`, `http://localhost:3000`; test default host `testserver`; add `SecurityMiddleware`, `CsrfViewMiddleware` and the new headers middleware in a safe order (request-logging middleware stays first)
- `apps/kernel/security_headers.py` -- middleware setting the headers; API-only CSP `default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`
- `apps/kernel/links.py` -- `frontend_url(path)`; `apps/kernel/network.py` -- `client_ip(request)` using `settings.TRUSTED_PROXIES`
- `.env.example`, `scripts/bootstrap_env.py`, `README.md`, CI env -- new variables documented with placeholders only
- `tests/security/` -- tests for every matrix row (prod settings via the same approach as `tests/test_prod_settings.py`)

## Tasks & Acceptance

**Execution:**
- [x] `config/env.py`, `config/settings/*` -- validated hosts, origins, cookies, HSTS, base URL, trusted proxies -- safe configuration
- [x] `apps/kernel/security_headers.py`, middleware wiring -- headers and CSP on every response
- [x] `apps/kernel/links.py`, `apps/kernel/network.py` -- link builder and trusted-proxy resolver
- [x] `.env.example`, `scripts/bootstrap_env.py`, `README.md`, CI -- variables and docs
- [x] `tests/security/` -- cover every matrix row

**Acceptance Criteria:**
- Given the settings, when the app starts, then hosts and trusted origins come from the environment with no wildcard, and cookies are HttpOnly and SameSite=Lax (Secure outside local).
- Given any response, when it is returned, then the security headers and a CSP are present, and links are built from `FRONTEND_BASE_URL`, never the Host header.
- Given the production settings, when I enumerate routes, then the Django admin is absent.
- Given forwarded headers, when a request arrives, then they are trusted only from the configured proxy list.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Pass 1 (Blind Hunter, Edge Case Hunter, Verification Gap).

| # | Finding (source) | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | CsrfViewMiddleware never exercised by a test (BH, VG) | high | patch | Removing it left every test green; enforce_csrf_checks tests added. |
| 2 | CSRF failures return Django HTML, not the envelope (EC) | medium | patch | CSRF_FAILURE_VIEW now returns the envelope. |
| 3 | Prod CSRF origins may be http while cookies are Secure (BH) | medium | patch | https required in prod; empty still allowed. |
| 4 | Origin/base URL accept `?`, `#`, `@` (EC) | low | patch | Rejected at startup. |
| 5 | Test settings break locally without `testserver` (BH, EC) | medium | patch | Always appended in test settings. |
| 6 | client_ip: ports, IPv4-mapped IPv6, empty REMOTE_ADDR, inconsistent return, re-parsing, too-broad ranges (BH, EC) | medium | patch | Normalised, cached, ranges below /8 and /16 refused. |
| 7 | frontend_url accepts percent-encoded slashes, `..`, `?`, `#` (BH) | medium | patch | Rejected with tests. |
| 8 | Weak header assertions, vacuous test assert, unused fixtures (BH, VG) | low | patch | Exact values asserted. |
| 9 | Local settings module unexercised (VG, EC) | low | patch | Smoke test added. |
| 10 | README gaps on TRUSTED_PROXIES and CSRF origins (BH) | low | patch | Lines added. |
| 11 | CSP overwritten unconditionally, blocks future docs pages (EC) | low | reject | No HTML page exists yet; revisit with the first one. |
| 12 | Extra headers (CORP, Cache-Control), IDN/IPv6 host forms, settings duplication, CHANGELOG (BH, EC) | low | reject | Not required by the story; fix adds complexity. |
| 13 | HSTS emitted without is_secure, SecurityMiddleware overlap (BH) | low | reject | Deliberate: TLS ends at the proxy; documented in Design Notes. |
| 14 | bootstrap_env.py unchanged (EC) | false | reject | New variables carry no secrets and have defaults. |
| 15 | Prod route enumeration limited, client_ip has no caller (EC, BH, VG) | low | reject | Admin app is not installed in prod; caller arrives in Story 1.7. |

## Design Notes

Decisions made without asking: the Django admin stays absent in every environment for now because it requires the auth and sessions apps that Story 1.12 replaces with the custom Account model; the CSRF cookie is HttpOnly and the CSRF token will be served by an endpoint with sign-in (Story 1.17); TLS terminates at the proxy, so `SECURE_SSL_REDIRECT` stays off and HSTS is set in prod only; the CSP is API-only and the frontend repo sets its own for pages; an empty `TRUSTED_PROXIES` trusts nobody.

## Verification

**Commands:**
- `uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run pytest` -- expected: all pass
- `uv run python manage.py spectacular --validate --file openapi.yaml && git diff --exit-code openapi.yaml` -- expected: no diff
