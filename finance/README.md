# Navin Finance Intelligence

Working internal finance-control application for Dadri, Vaishali, Sikandrabad and Meerut Road, Ghaziabad. Entities, branches, accounts, departments, vendors, panels and rules are configurable. Thirteen workspaces cover executive review, revenue, collections, procurement, treasury, expenses, stock, assets/debt, statements, budgets, exceptions, close and data setup.

## Run

Python 3.12; FastAPI, SQLite and plain browser JavaScript. No LLM or API key is required.

```bash
bash scripts/install.sh
bash scripts/start.sh
```

For HTTPS deployment set `NAVIN_COOKIE_SECURE=true`. Set `NAVIN_HOST=0.0.0.0` only when a hosting platform requires it. `PORT` defaults to 8000. Put a HTTPS reverse proxy in front of the server; trust forwarded headers only from its localhost connection. `/health` verifies database connectivity.

First startup creates `admin`. Its random password is stored privately in `data/initial-admin.txt`; alternatively securely inject `NAVIN_ADMIN_PASSWORD` before the first startup. Existing accounts are never reset. Change the password in Imports & Settings → Access. Create a different finance user for independent approval: a maker cannot approve their own changes. The read-only `reviewer` role is for inspection; `finance` performs approvals.

## Begin using real records

The initial screen uses a clearly marked synthetic demonstration workspace. Switch the top-right selector to **Real finance records** to begin. It is empty and missing metrics remain unknown.

1. Create a legal entity and assign each relevant branch to it in Masters. Finance master changes need a different finance user's approval.
2. Create reviewed ledger account categories, banks, vendors, panels and other needed masters.
3. Download a canonical CSV template, or upload an actual CSV/XLSX report. Review every worksheet, header row, column meaning, date, identifier and sign. Inspect accepted/rejected/excluded records and overlap warnings before committing. Original files and values remain retained.
4. Import journals or compatible trial balance snapshots for accounting results; final bills and collection allocations for receivables; invoices and settlement allocations for payables; bank balances/movements for treasury. Add other registers when available.
5. Review coverage before approving completeness settings. Enter materiality, payment terms, contract rates and scenario assumptions explicitly. Missing inputs never authorize invented values.

The original uploaded texts were software specifications, not hospital source reports. Parsers support reviewed canonical mappings; correctness of any hospital export must still be validated against that actual file. [Source mapping](docs/SOURCE_MAPPING.md) explains this process.

## Validation

```bash
.venv/bin/python -m pytest -q
node --check app/static/app.js
```

The browser test uses `/usr/bin/chromium`, Playwright and a disposable database. It visits all thirteen modules, checks real/demo isolation and mobile layout. Financial tests cover allocations, approvals, bank matching, stock, debt, budgets, forecasts, imports, rollback, locks, permissions and backup restore. Screenshots in `docs/preview*.png` contain synthetic data only.

## Data and recovery

`NAVIN_DATA_DIR` defaults to `finance/data/`; it contains the SQLite database, uploaded originals and bootstrap credentials. It is excluded from Git. Back up before upgrades. Archives contain confidential data and user password hashes; bootstrap plaintext credentials are excluded.

```bash
.venv/bin/python -m app.backup backup /secure/path/finance-backup.tgz
# Stop the application before restore; destination must be a new directory.
.venv/bin/python -m app.backup restore /secure/path/finance-backup.tgz /secure/path/restored-data
# Start with NAVIN_DATA_DIR=/secure/path/restored-data
```

Restore verifies SQLite integrity, rewrites original upload paths and removes sessions. Store backups privately and test recovery periodically. Schema version 1 is recorded in `migrations`; startup is idempotent. There is no automatic future schema migration or retention deletion policy.

## Temporary preview

The existing user-authorized Tunnelmole helper outside this repository forwards the HTTPS preview to port 8000. This is a temporary workspace preview: it stops when its process/workspace stops, and its URL can change upon restart. It is not a permanently deployed Git-connected service.

See [delivery checklist](docs/DELIVERY_CHECKLIST.md) for the exact supported scope and remaining limits.
