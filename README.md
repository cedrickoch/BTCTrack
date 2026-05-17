# BTCTrack

Self-hosted, privacy-preserving Bitcoin portfolio tracker. Connects to your own
Electrum/Electrs/Fulcrum server, supports xpub and single-address watch-only
imports, and excludes wallet-to-wallet transfers from performance figures.
Realised gains are computed with FIFO lot accounting.

## Features

- Watch-only import of `xpub` / `ypub` / `zpub` (BIP44 / 49 / 84 / 86) and bare BTC addresses
- Configurable gap-limit scan of receive + change chains
- Auto-detects internal transfers between your own wallets — they don't pollute P&L
- FIFO realised gains computed against historical CHF / EUR / USD prices
- Streamlit dashboard with portfolio value, cost basis line, per-wallet breakdown
- Local SQLite storage; everything stays on your machine
- Single Docker container, LAN-only deployment by default

## Screenshots

- [Dashboard](screenshots/screenshot_dashboard.png)
- [Wallets](screenshots/screenshot_wallets.png)
- [Transactions](screenshots/screenshot_transactions.png)
- [Settings](screenshots/screenshot_settings.png)

## Stack

Python 3.12 · Streamlit · SQLAlchemy · aiorpcx (Electrum) · bip-utils · bundled BTC/USD + ECB FX price snapshot.

## Quick start (Docker)

```bash
docker compose up --build -d
open http://localhost:8501
```

It runs on sensible defaults out of the box. To point at your own Electrum
server (or change the base currency / gap limit), set the matching variable in
your shell before `docker compose up` — e.g. `ELECTRUM_HOST=bitcoin
ELECTRUM_PORT=50001 docker compose up -d` — or edit the `environment:` block in
`docker-compose.yml`.

The build step downloads the daily BTC/USD history from Bitstamp's public
OHLC API and the daily USD→EUR / USD→CHF rates from Frankfurter (ECB
reference rates). Both are free and require no account. The CSV is baked
into the image; the running container never calls out for prices.

In the UI (runtime config — Electrum host, base currency, … — comes from
environment variables, see `docker-compose.yml`; there is no in-app config):

1. **Wallets** → add an `xpub` (label it, pick script type, default gap limit 20) or a single address.
2. Click **Run sync** on the Settings page.
3. **Dashboard** shows total BTC, current fiat value, open-lot cost basis, unrealised + realised P&L, and a value-vs-cost-basis chart. **Transactions** lists every tx with a classification badge (`internal` / `external_in` / `external_out` / `unknown`).

## Quick start (bare Python, dev)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
streamlit run src/btctrack/ui/app.py
```

Runs on defaults; prefix any setting as an env var to override, e.g.
`ELECTRUM_HOST=bitcoin streamlit run src/btctrack/ui/app.py`.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

## Price data

The daily BTC/USD/EUR/CHF history is generated at `docker build` time by
`scripts/build_price_snapshot.py` and shipped inside the image as
`btctrack/prices/data/btc_prices.csv`. The running app reads this file with
`importlib.resources` and makes **no outbound HTTP calls** for price data.
Dates after the build day stay unpriced — rebuild the image to refresh.

Two no-account public APIs feed the build:

- **Bitstamp** — paginated daily BTC/USD OHLC closes, full history back to
  2011-08-18 (~5 calls).
- **Frankfurter** (<https://api.frankfurter.dev>) — the European Central
  Bank's free FX reference rates for daily USD→EUR / USD→CHF. ECB only
  publishes on weekdays, so weekend / holiday FX rates are forward-filled
  from the most recent business-day rate.

BTC-EUR and BTC-CHF in the snapshot are computed as BTC-USD × FX. To run
the build outside Docker:

```bash
.venv/bin/python scripts/build_price_snapshot.py \
    --output src/btctrack/prices/data/btc_prices.csv
```

## Privacy notes

- The Electrum connection should point at **your own** server. Public Electrum
  servers see every address you query and can correlate them.
- Price lookups are local-only against the bundled snapshot. Only the build
  host (during `docker build`) talks to Bitstamp and Frankfurter, and even
  then only a date range and currency codes leak — never any addresses.
- The default deployment binds to all interfaces inside the container but only
  publishes port 8501 to your LAN. To expose it remotely, terminate TLS in front
  (Caddy, Traefik, Tailscale) — there is no built-in auth.

## Privacy mode (mask values)

All fiat amounts, BTC/sat amounts, and the Dashboard chart can be hidden behind
a password. A lock widget appears in the Streamlit sidebar; the app starts
**masked on every reload** and stays unlocked only for the current browser
session.

Set up: go to **Settings → Privacy mode** and set a password. The bcrypt hash is
stored in the database (and travels with backups). The password is **immutable**
once set — it cannot be changed, shown, or removed from the UI. Leaving it unset
disables the feature entirely (no sidebar widget, no masking).

## Migrating between hosts

Settings → **Export data** snapshots the SQLite DB (wallets, addresses,
transactions, lots, realised gains, settings) into a single file.

- Default mode is **encrypted with a passphrase** (AES-256-GCM, PBKDF2-HMAC-SHA256
  with 600k iterations). Use this whenever the file leaves the host — USB stick,
  scp, cloud sync — since it contains your xpubs.
- Uncheck the encryption box for a raw `.btctrk` file (the SQLite bytes with an
  8-byte header). Useful only for local debugging.

On the destination host, Settings → **Import data**, upload the file, enter the
passphrase. The current DB is moved to `btctrack.db.bak` before the swap, and
the import is rejected (live DB untouched) if the passphrase is wrong, the file
is tampered with, or the schema doesn't match. Runtime config (Electrum
endpoint, base currency, …) lives in environment variables, not the DB — set
them on the new host for that machine's Electrum endpoint.

## End-to-end manual verification

1. Set `ELECTRUM_HOST` / `BASE_CURRENCY=CHF` (see `docker-compose.yml`), `docker compose up --build`, open `http://localhost:8501`.
2. Wallets → add a known cold-storage `zpub` and a hot-wallet single address.
3. Settings → **Run sync**. Then check:
   - Transactions: a historical exchange withdrawal appears as `external_in` with the correct CHF cost basis at the block date.
   - A self-transfer cold→hot is `internal` and does **not** show up in realised/unrealised P&L.
   - A spend to a third party is `external_out` and emits a `realized_gain` row.
4. Dashboard total BTC equals the sum of wallet balances; the chart starts at the first `external_in`.

## Out of scope (planned follow-ups)

- Tax export CSV
- Manual classification override for `unknown` txs
- Authentication / multi-user
