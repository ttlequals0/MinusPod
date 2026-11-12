# Session length settings and "Remember me": design

Issue #996, items 1 and 2. Ships in 2.102.0.

## Context

A signed-in session lasts 24 hours from its last use. The length comes from
`SESSION_LIFETIME_HOURS`, read once at startup
(`src/main_app/__init__.py`). It is documented in
`docs/environment-variables.md` and nowhere else: neither compose file passes
it into the container, so setting it in `.env` on a stock install does
nothing.

An operator who reaches the UI through their own SSO still types the MinusPod
password about once a day on every device. The only way to change that today
is a longer session for every login, including one on a borrowed device.

Sessions are Flask signed cookies with one global lifetime. Flask cannot give
two sessions different lifetimes as the code stands.

## Goal

The login page has a "Remember me on this device" checkbox that gives that
device a longer session. Both lengths are settings an operator can change from
the Security section or the API, with no restart.

## Non-goals

- Trusting an identity header from a reverse proxy (item 3 of the issue). It
  needs its own design.
- A list of signed-in devices or signing out one device remotely. Changing the
  password still signs out every device.
- Changing what a session is: it stays a signed cookie with no server-side
  store.

## Decision: two settings

| Setting | Env var that seeds it | Default | Range |
|---|---|---|---|
| Session length, hours | `SESSION_LIFETIME_HOURS` | 24 | 1 to 8760 |
| "Remember me" length, days | `SESSION_REMEMBER_DAYS` | 30 | 1 to 365 |

Both live in the settings registry as env-backed settings, the way
`llm_provider` does (`ENV_BACKED_SETTINGS` and `get_env_backed_int` in
`src/config.py`): the env var gives the value on first start, and a value
saved through the API wins from then on. They are read through
`GET /api/v1/settings` and saved through `PUT /api/v1/settings/ad-detection`,
the route the Security section already uses for its blocked user agent list, with defaults,
validation and reset. `src/config_transfer.py` includes them in config export
and import.

Both lengths are idle limits, as the session length is today: each use of the
session starts the period again.

## Decision: per-session length

`POST /api/v1/auth/login` accepts an optional `rememberMe` boolean, default
false. A session records whether it is remembered and when it was last used.

`session_is_authenticated` in `src/api/auth_state.py` already runs on every
API request. It also compares the time since last use with the limit for that
session, the hours setting or the days setting, and treats a session past its
limit as signed out. The last-used time is refreshed at most once a minute.

Flask's own lifetime is set to the largest value the settings allow, 8760
hours, so the cookie signature never expires before the app's check does. The
session cookie's expiry is set per session to match its limit. Flask computes
that expiry from the global lifetime when it saves the session, after the
`after_request` hooks have run. This needs a small subclass of
`SecureCookieSessionInterface` that overrides only `get_expiration_time`.

A changed setting applies to existing sessions on their next request.
Shortening one signs out sessions already idle past the new limit.

Setting or changing the password signs the caller back in, as today, and
keeps that session's remembered state.

Rate limits and the failed-login lockout are not touched. They run before the
password is checked.

## Decision: cookies

The change in 2.101.34 stands: a response to an anonymous request sets no
cookie. The remembered state is applied inside the login request.

The CSRF cookie has no expiry and goes when the browser closes. A remembered
session outlives it. The cookie is issued again on the next authenticated
response, and the UI calls `/auth/status` when it loads, so the first change
after a browser restart still carries the token.

## Decision: UI

Follows `design-guide/UI-CONTRACT.md`.

- Login page: a "Remember me on this device" checkbox under the password
  field, unchecked by default. Its label is the click target.
- Login page: a 429 shows "Too many sign-in attempts. Try again in N
  minutes." Today it shows "Invalid password". Both sources of a 429 on this
  route, the rate limit and the failed-login lockout, send `Retry-After` in
  seconds. The lockout sends its unlock time today.
- Settings, Security section: "Session length" in hours and "Remember me
  length" in days, each with a line of help. The help for the second says it
  applies only to sign-ins that tick the checkbox. The section saves its own
  values today, so the two fields get a Save button in that section and
  register their draft with `useUnsavedChanges`.

## Decision: Compose and docs

- `docker-compose.yml` and `docker-compose.cpu.yml` pass both variables in the
  Security block, in the existing `${NAME:-default}` style.
- `.env.example` lists both.
- `docs/environment-variables.md` lists `SESSION_REMEMBER_DAYS` and says a
  value saved in the UI takes over from either variable.
- `docs/security-and-storage.md` explains both lengths and the trade-off of a
  long session on a shared device.
- `openapi.yaml` and `docs/api-and-webhooks.md`: `rememberMe` on login, the
  429 the route already returns, and the two settings.
- `CHANGELOG.md`.

## Tests

- Settings: validation, defaults, reset, env seeding, a saved value winning
  over the env var, config export and import.
- Login: `rememberMe` true and false, omitted, and a non-boolean value.
- Expiry: a normal session past the hours limit is signed out, a remembered
  one is not, a remembered one past the days limit is, and use inside the
  limit extends it.
- A changed setting applies to an existing session without a restart.
- Password change signs out other sessions and keeps the caller's remembered
  state.
- Cookies: the session cookie's expiry matches its limit, an anonymous
  response still sets no cookie, and the CSRF cookie is issued again after a
  browser restart on a remembered session.
- OpenAPI conformance for the changed routes.
- Frontend: vitest for the checkbox, the lockout message and the Security
  fields, plus Playwright screenshots at the UI-CONTRACT widths with a coarse
  pointer at phone widths.

## Alternatives considered

- Raising the session length for everyone. It also applies to a one-off
  sign-in on a device the operator does not own.
- Env vars only, as the issue proposed. A change would need a container
  restart, and the setting would be invisible in the UI.
- A server-side session store. It would allow per-device sign-out, and it is a
  larger change than this needs.
