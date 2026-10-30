# Order and Portfolio-Risk Persistence Contract

This document is the behavioural contract for `stratex_agents.order_store.OrderIntentStore`
and `stratex_agents.portfolio_risk_state.PortfolioRiskStateStore`. It describes what the
public methods guarantee, independent of the SQL backend.

## 1. Backend selection

Both stores resolve their target in this order:

1. An explicit `path` argument to the constructor. It may be a local SQLite path, a
   PostgreSQL DSN or an Oracle DSN.
2. `STRATEX_DATABASE_URL`, when set. It must start with one of:
   - PostgreSQL: `postgresql://`, `postgres://`, `postgresql+psycopg://`
   - Oracle: `oracle+oracledb://`, `oracle://`, `oracledb://`
   Any other value raises `RuntimeError` and no local file is created.
3. Backend-specific local path:
   - `OrderIntentStore`: `STRATEX_ORDER_DB` (default `state/stratex_orders.sqlite3`).
   - `PortfolioRiskStateStore`: `STRATEX_PORTFOLIO_RISK_DB`
     (default `state/portfolio_risk.sqlite3`).

`OrderIntentStore` additionally fails closed when `STRATEX_ORDER_MANAGER_ENABLED` or
`STRATEX_RECONCILIATION_ENABLED` is true and no `STRATEX_DATABASE_URL` is configured:
a local SQLite ledger is never substituted for a production OMS database.

SQLite is supported only as an explicit path for unit tests/local development.
`PortfolioRiskStateStore` has no OMS fail-closed guard; that is unchanged from the
pre-Oracle implementation.

PostgreSQL is reached through `psycopg` 3. Oracle is reached through SQLAlchemy 2.x
(`oracle+oracledb`) and `python-oracledb`. Both are imported only when the Oracle
backend is selected, so PostgreSQL/SQLite deployments do not require the Oracle
driver.

Oracle unqualified names resolve to the schema that owns the connection. Production
uses the Oracle user/schema `STRATEX`.

## 2. `OrderIntentStore`

### 2.1 Constructor

`OrderIntentStore(path: str | None = None)`

Creates the schema on first use and raises if the database is unreachable or the
schema cannot be created. On return, `_use_postgres`, `_use_oracle` and `_dialect`
reflect the selected backend. These attributes are informational; the public
contract is the methods below.

### 2.2 Terminology

- **Intent**: one row in `order_intents`, keyed by `client_order_id`.
- **payload hash**: caller-supplied identity hash. Stratex uses the SHA-256 of the
  intent except for `created_at`.
- **terminal**: `ACCEPTED`, `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, `REJECTED`,
  `UNKNOWN`. `ACCEPTED` and `PARTIALLY_FILLED` may still progress (fill/cancel);
  `FILLED`, `CANCELLED` and `REJECTED` are final. `UNKNOWN` means the venue outcome
  is ambiguous and must be reconciled before any retry.
- **TTL/expiry**: none. There is no time-based expiry and no automatic deletion.
  Duplicate protection is status-based. `created_at`/`updated_at` are Unix epoch
  seconds used for ordering and reporting only.

### 2.3 `reserve(client_order_id, payload_hash, payload) -> dict`

Atomically claims an ID. The primary key on `client_order_id` is the exclusivity
primitive.

- If the row did not exist, inserts it with `status = "RESERVED"`, appends one
  `INTENT_RESERVED` audit event, and returns
  `{"kind": "new", "status": "RESERVED"}`.
- If the row exists and `payload_hash` differs, raises `IntentConflictError`
  (`"client_order_id already belongs to a different trade intent"`). Nothing is
  written.
- If the row exists with status `RESERVED`, `SUBMITTING` or `UNKNOWN`, raises
  `IntentInFlightError`. Resubmission could duplicate a fill; the caller must
  reconcile first.
- Otherwise (any terminal/final status) appends one `DUPLICATE_REQUEST` audit event,
  does not modify the intent, and returns
  `{"kind": "duplicate", "status": <stored status>, "result": <parsed result_json or None>,
  "error": <stored error_text or None>}`.

The `INTENT_RESERVED` audit details contain `payload_hash` plus only the allow-listed
payload fields `venue`, `symbol`, `side`, `environment`, `quantity`, `strategy`.
Arbitrary metadata (including credentials) is never copied into the audit ledger.

Concurrent first reservations of the same ID have exactly one winner. PostgreSQL uses
`INSERT ... ON CONFLICT DO NOTHING RETURNING`; SQLite uses `BEGIN IMMEDIATE`; Oracle
attempts `INSERT`, translates the unique-key error (`ORA-00001`), then locks the
existing row with `SELECT ... FOR UPDATE` and applies the same conflict/in-flight
rules.

### 2.4 `transition(client_order_id, status, *, result=None, error=None) -> None`

Validates the target status against `PROGRESSIONS`, updates the row, and appends one
`ORDER_STATE_TRANSITION` audit event. Unknown IDs raise `KeyError`. Unsupported
targets and invalid progressions raise `ValueError` with
`"Invalid order lifecycle transition: <old> -> <new>"`.

Allowed transitions (including idempotent self-transitions):

| Current | Allowed next |
|---|---|
| `RESERVED` | `RESERVED`, `SUBMITTING`, `ACCEPTED`, `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, `REJECTED`, `UNKNOWN` |
| `SUBMITTING` | `SUBMITTING`, `ACCEPTED`, `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, `REJECTED`, `UNKNOWN` |
| `UNKNOWN` | `UNKNOWN`, `ACCEPTED`, `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, `REJECTED` |
| `ACCEPTED` | `ACCEPTED`, `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, `REJECTED` |
| `PARTIALLY_FILLED` | `PARTIALLY_FILLED`, `FILLED`, `CANCELLED` |
| `FILLED` | `FILLED` |
| `CANCELLED` | `CANCELLED` |
| `REJECTED` | `REJECTED` |

Column semantics:

- `status` is always replaced.
- `result_json` is replaced only when `result is not None`; a `None` result preserves
  the previous value (PostgreSQL `COALESCE`, explicit read-back on Oracle).
- `error_text` is replaced on every transition. A `None` error clears it.
- `updated_at` is set to the transition timestamp.

Audit details contain a bounded result summary (allow-listed keys: `status`,
`order_id`, `deal`, `avg_price`, `price`, `quantity`, `volume_filled`, `venue`,
`environment`, `protection_verified`, `stop_algo_id`, `take_profit_algo_id`,
`reason`) and the error truncated to 300 characters. Full venue responses are not
copied into the audit ledger.

Oracle takes a row lock (`SELECT ... FOR UPDATE`) before validating and updating so
two transitions for one intent serialise. PostgreSQL/SQLite keep their previous
read-then-update behaviour.

### 2.5 `get(client_order_id) -> dict | None`

Returns `None` if absent, otherwise a lower-case-keyed dict with:

- `client_order_id`, `status`, `created_at`, `updated_at`, `error_text`
- `payload`: parsed `payload_json`
- `result`: parsed `result_json`, or `None`

`payload_hash` is deliberately omitted. Oracle CLOB values are returned as text, never
as driver LOB objects.

### 2.6 `list_reconcilable(limit=100) -> list[dict]`

Returns rows with status in
`RESERVED, SUBMITTING, UNKNOWN, ACCEPTED, PARTIALLY_FILLED`, oldest `created_at`
first. `limit` is clamped to `1..1000`. Each row has
`client_order_id, status, created_at, updated_at, error_text`.

### 2.7 `list_unresolved(limit=100) -> list[dict]`

Same shape, but only `RESERVED, SUBMITTING, UNKNOWN`; `limit` clamped to `1..1000`.

### 2.8 `record_correction(client_order_id, *, actor, reason, correction) -> None`

Appends a `MANUAL_CORRECTION` audit event without changing the intent. Raises:

- `ValueError` if `actor` or `reason` is empty after stripping, if `correction` is not
  a non-empty dict, or if it has keys outside
  `field, old_value, new_value, reference`.
- `KeyError` if the intent does not exist.

`actor` is truncated to 100 characters and `reason` to 500 in the audit details. The
event's `previous_status`/`new_status` are the current stored status (unchanged).

### 2.9 `list_audit_events(*, client_order_id=None, limit=1000) -> list[dict]`

Returns audit rows in ascending `event_id` order. `limit` is clamped to `1..10000`.
Each item has `event_id, client_order_id, event_type, previous_status, new_status,
details` (parsed JSON) and `created_at`. The ledger is append-only at the database
level:

- SQLite: `BEFORE UPDATE`/`BEFORE DELETE` triggers raise
  `"audit events are append-only"`.
- PostgreSQL: trigger functions raise the same message.
- Oracle: `BEFORE UPDATE`/`BEFORE DELETE` triggers call
  `raise_application_error(-20001, 'audit events are append-only')`.

There is no method to update or delete audit events.

## 3. `PortfolioRiskStateStore`

### 3.1 Constructor and table

`PortfolioRiskStateStore(path: str | None = None)` creates
`portfolio_equity_state` if missing. The primary key is
`(account_key, reporting_currency, utc_day)`.

### 3.2 `observe(*, account_key, reporting_currency, equity, timestamp=None) -> dict`

Validates:

- `account_key` and `reporting_currency` are non-empty after stripping; currency is
  upper-cased and must be at most 12 characters.
- `equity` and `timestamp` are finite; `equity > 0`.
- `utc_day` is derived from `timestamp` in UTC (`YYYY-MM-DD`); default timestamp is
  `time.time()`.

Semantics:

- First observation for an `(account, currency, day)` inserts
  `day_start_equity = high_water_equity = equity`.
- Later observations preserve `day_start_equity` and set
  `high_water_equity = max(previous high water, equity)`.
- `updated_at` is the observation timestamp.

Returns `utc_day`, `day_start_equity`, `high_water_equity`, `equity`,
`reporting_currency`, `captured_at`, `equity_basis="account_equity"`.

On Oracle the read-then-insert/update is a single `MERGE` on the composite key, so a
concurrent first observation cannot lose the baseline or regress the high-water mark.
PostgreSQL and SQLite keep the pre-existing read-then-write implementation.

### 3.3 `get_baseline(*, account_key, reporting_currency, utc_day=None) -> dict | None`

Returns `None` when no row exists for the UTC day (default: today in UTC). Otherwise
returns `account_key`, `reporting_currency`, `utc_day`, `day_start_equity`,
`high_water_equity`, `updated_at`.

### 3.4 `enrich_snapshot(snapshot, *, account_key) -> dict`

Returns a copy of `snapshot` updated with `observe(...)`. Refuses, with
`PortfolioRiskStateError`:

- non-dict or `status != "healthy"`;
- `currency_conversion_complete is not True`;
- `equity_basis != "account_equity"`.

The risk maths and thresholds live in the callers; this store only persists the
baseline/high-water state.

## 4. Oracle schema (schema owner `STRATEX`, unqualified names)

### `order_intents`

| Column | Type | Notes |
|---|---|---|
| `client_order_id` | `VARCHAR2(128 CHAR)` | primary key |
| `payload_hash` | `VARCHAR2(128 CHAR)` | not null |
| `payload_json` | `CLOB` | not null; JSON text |
| `status` | `VARCHAR2(32 CHAR)` | not null |
| `result_json` | `CLOB` | JSON text or null |
| `error_text` | `CLOB` | null |
| `created_at` | `BINARY_DOUBLE` | not null; Unix epoch seconds |
| `updated_at` | `BINARY_DOUBLE` | not null; Unix epoch seconds |

Index: `idx_order_intents_status (status)`.

### `order_audit_events`

| Column | Type | Notes |
|---|---|---|
| `event_id` | `NUMBER GENERATED BY DEFAULT ON NULL AS IDENTITY` | primary key |
| `client_order_id` | `VARCHAR2(128 CHAR)` | not null, FK to `order_intents` |
| `event_type` | `VARCHAR2(64 CHAR)` | not null |
| `previous_status` | `VARCHAR2(32 CHAR)` | null |
| `new_status` | `VARCHAR2(32 CHAR)` | null |
| `details_json` | `CLOB` | not null; JSON text |
| `created_at` | `BINARY_DOUBLE` | not null; Unix epoch seconds |

Index: `idx_order_audit_client_time (client_order_id, created_at)`.
Triggers: `order_audit_no_update`, `order_audit_no_delete` (append-only).

### `portfolio_equity_state`

| Column | Type | Notes |
|---|---|---|
| `account_key` | `VARCHAR2(256 CHAR)` | part of primary key |
| `reporting_currency` | `VARCHAR2(32 CHAR)` | part of primary key |
| `utc_day` | `VARCHAR2(10 CHAR)` | part of primary key, `YYYY-MM-DD` |
| `day_start_equity` | `BINARY_DOUBLE` | not null |
| `high_water_equity` | `BINARY_DOUBLE` | not null |
| `updated_at` | `BINARY_DOUBLE` | not null; Unix epoch seconds |

Bootstrap checks `ALL_TABLES`, `ALL_INDEXES` and `ALL_TRIGGERS` for
`SYS_CONTEXT('USERENV','CURRENT_SCHEMA')`, creates only missing objects, logs every
creation/bootstrapped schema, and re-raises every failure. Concurrent creation races
are tolerated only when the object is then visible (ORA-00955). Existing tables are
not migrated; schema drift must be handled by an explicit migration.

## 5. SQL translation summary

| PostgreSQL / SQLite construct | Oracle translation |
|---|---|
| `INSERT ... ON CONFLICT (id) DO NOTHING RETURNING id` | attempt `INSERT`; treat `ORA-00001` as "already exists"; `SELECT ... FOR UPDATE`; retry once if the row vanished |
| `ON CONFLICT ... DO UPDATE` (portfolio) | `MERGE INTO ... USING dual ON (...)` with matched `GREATEST` update and unmatched insert |
| `SELECT ... FOR UPDATE` | kept (`FOR UPDATE` is native Oracle) |
| `RETURNING` | not required; `rowcount` is checked after `UPDATE`, and reads happen in the same transaction |
| `LIMIT ?` | `FETCH FIRST :row_limit ROWS ONLY` |
| `?` positional binds | `:name` named binds through SQLAlchemy `text()` |
| boolean columns | no boolean columns exist; no translation required |
| `JSONB` / JSON text | `CLOB` + `json.dumps`/`json.loads` |
| `TEXT` for long diagnostics | `CLOB` (`payload_json`, `result_json`, `error_text`, `details_json`) |
| auto-increment audit key | `GENERATED BY DEFAULT ON NULL AS IDENTITY` |
| DB-level append-only triggers | `CREATE OR REPLACE TRIGGER ... raise_application_error` |

Reserved Oracle words are not used as column names. Oracle folds unquoted identifiers
to upper case, so every result row is normalized to lower-case keys before it leaves
the store.

## 6. Known Oracle-specific differences

- **Empty string is `NULL`.** PostgreSQL stores `''`; Oracle cannot. The contract
  never writes an empty `client_order_id`, `payload_hash`, `status`, `event_type`,
  `account_key`, `reporting_currency` or JSON document, so this only affects an
  explicit empty `error` argument (stored as `NULL` on Oracle).
- **Identifier/column size limits** are documented above. PostgreSQL `TEXT` was
  unbounded; diagnostics and JSON use `CLOB`, while IDs/statuses use bounded
  `VARCHAR2`.
- **No PostgreSQL→Oracle data migration** is performed. The Oracle tables start
  empty; cutover requires an explicit export/import or dual-run plan.
- **No Oracle live integration test** is bundled here. The Oracle SQL is exercised by
  dialect compilation and unit tests; a real `STRATEX_TEST_ORACLE_URL` integration
  job is required to prove connectivity/wallet configuration.
- **CLOB binds** use `CAST(:bind AS CLOB)` so values are stored as character data
  rather than being implicitly truncated to `VARCHAR2`.
