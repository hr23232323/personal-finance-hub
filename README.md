# Personal Finance Hub

A **local-first, privacy-first** personal finance hub you run on your own machine.
Pull your bank/transaction data into a local SQLite file, visualize it, and ask an
LLM advisor questions about your spending — like having a candid personal finance
person who's actually looked at your statements.

- **No hosted version, no account, no server collecting anything.** You clone it, you run it.
- **Your data lives in one local file** (`data/finance.db`), which is gitignored.
- **Model/provider-agnostic** LLM: point it at OpenRouter (default), OpenAI, a local
  model, or your own proxy — one config line.

---

## Where your data actually goes (read this)

There are two separate moments where data *could* leave your machine. Be deliberate about each:

| Feature | Data path | Privacy |
|---|---|---|
| **Manual import** (CSV/OFX/QFX) | Your bank's export → your machine. **Nothing else.** | 🟢 Fully local. Works with any bank, worldwide, free. |
| **SimpleFIN sync** | Bank → SimpleFIN + MX servers → your machine | 🟡 One trusted middleman retrieves your data, then copies it here. Convenience, not maximum privacy. |
| **Ask the advisor** (LLM) | Your question + the transactions relevant to it → your chosen LLM endpoint | 🔴 Leaves your machine when *you* ask. Off by default. Use a local model (Ollama) for zero-leak. |

> There is **no** way to auto-sync a bank with zero third party — banks don't allow it.
> Manual import is the only fully-local path; SimpleFIN is the low-friction convenience.
> The advisor only ever sees what you send it, when you send it.

---

## Quickstart

With **make** (easiest):

```bash
make setup     # create venv, install deps, create .env
make run       # start at http://127.0.0.1:8000
```

Run `make` on its own to see all commands (`run`, `dev`, `reset-data`, `clean`).

<details><summary>Or without make</summary>

```bash
# 1. Install (Python 3.10+)
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Configure (optional — needed only for the advisor)
cp .env.example .env
# edit .env and add an LLM_API_KEY (get one at openrouter.ai/keys)

# 3. Run
python -m app.api                  # open http://127.0.0.1:8000
```
</details>

The advisor is optional — the dashboard and file import work with no API key. Add
`LLM_API_KEY` to `.env` whenever you want to turn the advisor on.

### Try it with fake data first

Not ready to connect a real bank? Seed ~14 months of realistic activity across four
accounts (checking, savings, credit card, loan) so you can explore everything right away:

```bash
make seed      # fills the local DB with fake transactions
make run       # then open the dashboard
make reset-data   # wipe it whenever you want
```

The fake data includes a few patterns worth finding — lifestyle creep in dining, a
quietly recurring "forgotten" subscription, a holiday spike, and a summer trip — good
material for testing the advisor.

You can use the dashboard and import files **without any API key**. The advisor
turns on the moment you add `LLM_API_KEY`.

---

## Using it

### Import a file (fully local)
1. In your bank's website, download transactions as **CSV**, **OFX**, or **QFX**.
2. **Connect & import** tab → create an account → pick the file → **Import**.
3. Re-importing the same file is safe — duplicates are detected and skipped.

CSV columns are auto-detected (date / amount / description, or separate debit &
credit columns). Amount convention: **positive = money in, negative = money out**.

### SimpleFIN sync (auto-pull, US/Canada)
1. Sign up at [beta-bridge.simplefin.org](https://beta-bridge.simplefin.org) (~$15/yr),
   connect your bank there, and create a **setup token**.
2. **Connect & import** tab → paste the token → **Connect** → **Sync now**.
3. The token is exchanged once for a local access URL stored in your DB; you won't
   need the token again.

### Explore & discover
The **Statement** tab visualizes a period — category donut, an income→categories
**Sankey**, and a daily **spending calendar**.

The **Discoveries** tab is the point of the app, and it works in two tiers.

**Tier 1 — deterministic (always on, no API key needed).** Lenses computed exactly
from your data, ranked by how unusual each is *for you*, each clickable to the
transactions behind it:

- **Needs vs. discretionary** — the split, and how it's shifting over time
- **Big moments** — trips and one-time purchases aggregated (*"7 big moments = $6,633,
  8% of everything; excluding them you held around $5,426/mo"*)
- **Eating out** — restaurants vs. takeout vs. coffee, as a share of discretionary
- **Shopping** — frequency vs. order size (*"16 orders averaging $52"*)
- **Weekly rhythm** — which days you actually spend
- **Subscriptions** (the ones you can change) vs. **fixed commitments** (the ones you
  can't), small purchases, category trends, and possible duplicate charges

**Tier 2 — the coach (optional).** With an LLM key set, a short written read appears
above the feed: it connects the Tier-1 findings and picks out what matters. **Every
figure comes from Tier 1 — the model narrates the numbers, it never invents them.**
Without a key, everything above still works.

Charts use a vendored copy of ECharts (no CDN, fully offline).

### Ask the advisor
Go to **Ask the advisor** and ask things like *"Where did most of my money go last
month?"*, *"Any recurring subscriptions I might have forgotten?"*, *"What's a
purchase worth rethinking?"* The model answers by running read-only queries against
your local data (it can look, never change anything).

---

## Configuration (`.env`)

| Var | Default | Notes |
|---|---|---|
| `LLM_BASE_URL` | `https://openrouter.ai/api/v1` | Any OpenAI-compatible endpoint. OpenAI: `https://api.openai.com/v1`. Local Ollama: `http://localhost:11434/v1`. Your proxy: whatever URL. |
| `LLM_API_KEY` | *(empty)* | Required only for the advisor. `ollama` for local. |
| `LLM_MODEL` | `anthropic/claude-opus-4.1` | Any model your endpoint serves. |
| `DB_PATH` | `./data/finance.db` | Where your local data lives. |

**Want a local-only advisor?** Install [Ollama](https://ollama.com), run a model,
set `LLM_BASE_URL=http://localhost:11434/v1`, `LLM_API_KEY=ollama`,
`LLM_MODEL=llama3.1` — now even the advisor never leaves your machine.

---

## Architecture

```
app/
  config.py            env/.env config (no secrets in repo)
  db.py                SQLite schema + dedup-safe inserts
  models.py            NormalizedTransaction / NormalizedAccount
  categorize.py        simple keyword categorization
  connectors/
    manual.py          CSV + OFX/QFX parsing  (fully local)
    simplefin.py       token exchange + read-only sync
  queries.py           read-only queries (also the LLM's tools)
  analysis.py          detectors: recurring, anomalies, chart feeds
  classify.py          taxonomy: needs vs. discretionary, dining sub-types
  discover.py          Tier 1 — deterministic Discoveries engine
  coach.py             Tier 2 — LLM narration of Tier-1 facts (optional)
  llm.py               provider-agnostic client (tool-calling + completion)
  api.py               FastAPI: dashboard + JSON API (binds 127.0.0.1)
  web/                 vanilla-JS dashboard (no CDN, fully offline)
  web/vendor/          ECharts, vendored locally for offline charts
```

Single process, single SQLite file, no build step for the frontend.

## Roadmap / deliberately deferred
LLM-assisted categorization · budgets & goals · more connectors (Teller, Enable
Banking) · a maturer React frontend · encryption-at-rest for the DB. All have clean
seams; none are needed for v1.

## License
[MIT](LICENSE) — use it, fork it, build on it. Attribution appreciated but not required.
