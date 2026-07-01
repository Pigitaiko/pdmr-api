# DECISIONS.md — engineering decision log

Append-only. Each entry: context → decision → reasoning. Newest first within a session.

## Session 2026-07-01 (European expansion cont.)

### D-016 — Norway (Oslo Børs NewsWeb OAM): KRT-1500 form + harmonised template
**Context:** After the Nasdaq OAM unlock, Norway is the other Nordic OAM — Oslo Børs runs its *own*
NewsWeb (not on Nasdaq's). It exposes a clean public JSON API (`api3.oslo.oslobors.no/v1/newsreader`);
category **1102 = MANAGERS' TRANSACTION**; `robots.txt` is the SPA (no restriction). Detail messages
attach the Art. 19 data as a PDF.
**Decision:** `scraper/oslo_bors_no.py`, a structured source. list(cat=1102) → per message, download
each PDF attachment and parse by type:
- **KRT-1500** — Finanstilsynet's system-generated managers'-transaction form. Because it's
  government-generated its numbered Norwegian labels (`1.7.1 Fullt navn`, `2.2.2 Foretaksnavn`,
  `2.3.2 ISIN-kode`, `2.4.1 Transaksjonstype`, `2.8.1/2.8.2` price/volume, `2.9.1` date,
  `2.10.1 Handelsplass`) are identical across issuers → parses reliably. A trade via an associated
  legal entity (`1.6.2`) sets `is_legal_person` while keeping the PDMR as the person.
- **English EU-harmonised template** — some issuers attach this instead; we **reuse**
  `nasdaq_nordic.parse_mar_text` for it (same 2016/523 Annex).
- **body-only / custom PDFs** (SpareBank-style free-text prose, LTIP multi-person grants, takeover
  acceptances) → `partial` from the listing (issuer + country + title + link). Prose varies too much
  per issuer to extract reliably; not worth the fragility.
- Added Nordic transaction verbs to the shared parser (`KJØP/SALG/TEGNING`, sv/da/fi equivalents),
  which also lifts Nasdaq DK/SE nature-mapping.
**Result:** live batch parses form-based filings to full `success` (person + ISIN + NOK price/volume);
all filings covered at least as partials. 4 offline tests over a real KRT-1500 fixture. **11 → 12
markets.**
**Reasoning:** Same OAM pattern as Nasdaq, one national exchange; the KRT-1500 being a fixed
government form makes it the most reliable single template we parse.

### D-015 — Nasdaq Nordic OAM: six countries from one adapter (Finland → the unlock)
**Context:** Hunting for more countries, Finland (FIN-FSA) turned out **not** to publish managers'
transactions itself — it discloses them via "the message storage facility maintained by Nasdaq
Helsinki." That facility is Nasdaq Nordic's OAM (Officially Appointed Mechanism), a **single public
JSON news API** (`api.news.eu.nasdaq.com/news/query.action`) that serves the whole bloc: Helsinki,
Copenhagen, Reykjavík, Tallinn, Riga, Vilnius (+ Stockholm, which we skip — already covered by
`fi_sweden`). `robots.txt` is 404 (no restriction); filter with `cnscategory=Managers' Transactions`.
**Decision:** One adapter, `scraper/nasdaq_nordic.py`, registered as a structured source. It:
- queries the news API (English display), maps `market` → ISO country, **excludes Sweden**;
- **dedupes bilingual twins** — the API returns a separate item per language with distinct
  `disclosureId`s and localized company names (`Wärtsilä Corporation` vs `Wärtsilä Oyj Abp`), so we
  key on (releaseTime, market, person-in-headline), preferring the English rendering;
- carries the Art. 19 data two ways and handles both: a **PDF attachment** (the EU-harmonised
  2016/523 template — same Annex as Italy's Allegato 3F; anchored on the numbered section labels,
  language-independent for ISIN/LEI/date) **or** an **inline colon-labelled body** (Nasdaq's own
  format, mainly Helsinki). We prefer the inline body when present (cleaner than some scanned PDFs);
- falls back to a `partial` filing from listing metadata (issuer + country + ISIN + link) when the
  filing is in a local language we don't anchor (Danish/Lithuanian PDFs).
**Result (live):** ~50 filings/pull across FI/DK/IS/LT (EE/LV appear when present), **~52% full
`success`** with person + ISIN + price/volume, the rest useful partials. 10 offline tests over real
fixtures. This is the single biggest coverage jump: **5 → 11 markets** from one integration.
**Reasoning:** The OAM pattern (exchange-run disclosure hub) is how the Nordics/Baltics centralise
Art. 19; one API beats six national registers. ESAP would eventually do this pan-EU, but not until
2027–2028, so the Nasdaq OAM is the accessible shortcut today.

### D-014 — headless browser (Playwright) permitted by the user; France shipped
**Context:** After DE/UK/ES all proved inaccessible to plain HTTP (robots / WAF / JS-rendered SPAs),
the user explicitly lifted the brief's "no Selenium/headless" rule to reach UK, Spain and France.
**Decision:** Added Playwright (Chromium). Used it as a **discovery** tool — to find sites' backing
JSON APIs — rather than a runtime scraper wherever possible.
- **France (AMF) — SHIPPED, and needs no headless at runtime.** BDIF is an Angular SPA, but headless
  revealed its public JSON API (`/back/api/v1/informations?TypesInformation=DD`) + signed PDF path
  (`/back/api/v1/documents/<path>`), both reachable over plain HTTP. `scraper/amf_fr.py` uses raw
  httpx: API index → download the cleanly-labelled French declaration PDF → parse. 40/40 success live.
- **UK (FCA NSM) — still blocked.** The search API sits behind a WAF that returns a decoy
  ("Thanks for the visit") to non-browser clients and rejects even in-page `fetch` from headless
  Chromium ("Failed to fetch"). Bypassing it means an anti-bot arms race — not pursued.
- **Spain (CNMV) — not converged.** Results are JS-rendered and the search is per-issuer /
  autocomplete-driven, returning a "no data" widget on date-range queries; needs bespoke headless
  interaction scripting. Deferred.
**Reasoning:** Prefer discovering a clean HTTP API over driving a browser at runtime (faster,
deployable, no browser in prod). France fit that perfectly; UK/Spain would require running headless
in production and fighting anti-bot, which is heavier and fragile.

## Session 2026-06-30 (European expansion)

### D-013 — Germany (BaFin) is off-limits: robots.txt `Disallow: /`
**Context:** BaFin's managers'-transactions database (`portal.mvp.bafin.de/database/DealingsInfo`)
is technically scrapable — an empty-issuer + date-range POST returns a clean bulk table (issuer,
ISIN, PDMR, position, Buy/Sell, date, venue; no price/volume, which BaFin doesn't publish).
**But `https://portal.mvp.bafin.de/robots.txt` is `User-agent: * / Disallow: /`** — the operator
forbids all automated access to the entire portal.
**Decision:** Do **not** ship a BaFin scraper. Our standing policy (CLAUDE.md) is to honour
robots.txt; the PoliteClient enforced it and refused the request. The adapter and the
(improperly-recon'd) fixture were removed. Generic "Buy"/"Sell" nature keywords added during the
attempt are kept — they're source-agnostic.
**Reasoning:** Honouring robots.txt is the stated, ethical default; public data does not override an
explicit operator opt-out. Germany would need a licensed/official data feed, not scraping.
**Lesson:** check `robots.txt` *first* in per-country recon, before building.

### D-012 — France clean source dead → deferred
The data.gouv "Transactions des dirigeants" dataset points only to `lestransactions.fr`, which no
longer resolves (the third-party aggregator is gone). France therefore needs official AMF-site
scraping (PDF/BDIF) — Italy-magnitude work — and is deferred.

## Session 2026-06-19 (autonomous build, Phase 0+)

### D-001 — No pre-existing CLAUDE.md; authored from real data
**Context:** The brief treats `CLAUDE.md` as the pre-existing source of truth (verified PDF schema,
DB design, Nexi `2170-25-2026` ground truth, role/market codes). No `CLAUDE.md` and no repo existed
anywhere in the environment. The referenced "Nexi 2170-25-2026" filing was not retrievable.
**Decision:** Author `CLAUDE.md` myself, grounding every "verified" claim in **real** Allegato 3F
PDFs downloaded live from eMarketStorage. Use the real **CEMBRE `0088-10-2026`** filing as the
canonical fixture instead of the missing Nexi one, plus 6 more real filings for layout coverage.
**Reasoning:** Operating principle says make the reasonable call and keep going rather than wait
(user asleep). Inventing a fake "verified" schema would violate faithful reporting; deriving it from
real filings yields a *genuinely* verified schema and real test ground truth.

### D-002 — No Docker in build environment
**Context:** `docker`/`docker compose` are not installed and cannot be installed autonomously
(Desktop requires an admin GUI install). Several DoDs ("docker compose up brings up Postgres+Redis
healthy", "migration applies to Docker Postgres", "ingest 50 filings into Postgres") require a
running Postgres/Redis.
**Decision:** Author all Docker/Postgres/Redis code correctly (`docker-compose.yml`, async asyncpg
engine, alembic migration, Redis-backed scraper state + rate limit) so it runs on any machine that
*has* Docker. For executable tests here, run the DB/API suite against **`aiosqlite`** (SQLAlchemy is
DB-agnostic; SQLite ≥3.31 supports the `signal_value` computed column). Clearly mark in PROGRESS.md
which DoDs are "code-complete, executed on SQLite" vs "executed on Postgres".
**Reasoning:** Maximises real, runnable verification without the one unavailable dependency; keeps
the Postgres path honest and ready for the user's machine. Switching tactic per "if an approach is
blocked, switch and log it."

### D-003 — Package manager = uv
**Decision:** Use `uv` (installed 0.11.22; pins CPython 3.11.15). **Reasoning:** Env had only Python
3.9 and no poetry; uv installs a modern interpreter + fast locking with one tool. Noted per brief.

### D-004 — User-Agent contact = the public repo URL (reachable, not personal)
**Decision:** The committed default `USER_AGENT` (in `config.py`, `.env.example`, and the deployed
`render.yaml`) is `PDMR-API-bot/0.1 (+https://github.com/Pigitaiko/pdmr-api)`. Originally a
non-routable `contact@pdmr-api.example` placeholder; the pre-deploy audit flagged that as unreachable
(breaks the source-politeness policy for a public deploy), so it was switched to the repo URL.
**Reasoning:** "Respect the sources" wants a reachable contact; a public GitHub URL is reachable (via
issues) while keeping a personal email out of git history and out of every request header. An operator
can still override with a personal address in gitignored `.env` if they prefer.

### D-005 — Filing identity = `Comunicato n.` (`NNNN-NN-YYYY`)
**Decision:** Idempotency/dedup key is the eMarketStorage "Comunicato n." id (e.g. `0088-10-2026`),
present on the cover page and repeated in the footer `Fine Comunicato n.<id>`.
**Reasoning:** Stable, unique per filing, machine-extractable, matches the `NNNN-NN-YYYY` format the
brief itself referenced. Redis SET also tracks seen *source URLs* as a cheap pre-filter.

### D-009 — 1Info implemented via its JSON API (supersedes D-007's stub)
**Context:** User asked to add 1Info. Re-investigated the portal (a RequireJS/Knockout SPA).
**Decision:** Implemented 1Info **without a headless browser** by calling its backing JSON API
directly (reverse-engineered from the SPA bundles):
- Listing: `POST /PORTALE1INFO/API/Comunicati` — a DataTables server-side endpoint (requires the
  full `columns[]` form payload or it NREs). Returns every stored comunicato as JSON with issuer
  (`mittente`), title (`oggetto`), category, `pdf` id, and unix timestamps. We filter to
  internal-dealing by title.
- PDF: `GET /PdfViewer/PdfShow.aspx?username=oneinfo&password=oneinfo&type=comunicati&year={Y}&file={pdf}.pdf`
  where `{Y}` is the year embedded in the `pdf` id (`{ndg}_{seq}_{year}_oneinfo`). (Credentials are
  hardcoded in the site's own JS — public, not secrets we introduced.)
- 1Info PDFs carry no eMarketStorage "Comunicato n." id, so the listing supplies `filing_id`
  (the `pdf` id), issuer and publication date to the parser via `ListingItem.meta`.
**Reasoning:** Honours the no-Selenium constraint; the JSON API is far more robust than DOM scraping.

### D-010 — 1Info has many per-issuer Allegato 3F templates → partial-tolerant parsing
**Context:** Unlike eMarketStorage (one consistent rendering, ~97% clean parse), 1Info filings come
in **multiple per-issuer renderings** of the same legal form: bilingual "ALLEGATO/ANNEX", Italian-
only (e.g. DEXELANCE/EPH), and free-grant variants (e.g. De Nora, price 0, `Operazione N` without a
dash, `EUR 0 1420` price format). The parser was made section-based (split on header *phrases*, not
numbers) and tolerant of comma decimals, dot-grouped thousands, Italian dates, and `sede di
negoziazione` venues.
**Decision:** Ship with ~47% clean `success` on the live 1Info batch; the remainder degrade to
`parse_status='partial'` (every field that parses is kept; `raw_text` always retained) — never a
crash, never a dropped filing. Raising 1Info coverage is an ongoing, iterative tuning task (the
brief explicitly anticipated this: "let parse_status capture imperfect parses instead of crashing").
**Reasoning:** Matches the documented design intent; avoids over-fitting regexes reactively to an
open-ended set of bespoke issuer templates. A future improvement is a per-issuer template registry
or a layout-aware (column-position) extraction pass.

### D-007 — 1Info is a JS SPA → stub, not scrape (SUPERSEDED by D-009)
**Context:** `https://www.1info.it` 302-redirects to `/PORTALE1INFO`, a **Vue single-page app**
(only ~265 chars of server-rendered text; content is client-rendered). The brief forbids
Selenium/headless browsers and says: if a source genuinely requires JS, document it and stub the
scraper with a TODO.
**Decision:** `scraper/oneinfo.py` is a documented stub raising `NotImplementedError` with a TODO
describing the options (reverse-engineer the SPA's XHR/JSON API, or use the operator's official
data feed). eMarketStorage alone already provides the full internal-dealing flow.
**Reasoning:** Honours the no-headless constraint; avoids a brittle JS-execution dependency.

### D-008 — robots.txt compliance verified
eMarketStorage `robots.txt` disallows `/core /admin /user /search …` but **not** `/node/` or
`/sites/` — so listing pages (`/en/node/21`) and PDF paths (`/sites/default/files/comunicati/…`)
are permitted. Scraper still sends the descriptive UA, throttles ≥1 req/s, and backs off on 429.

### D-006 — One transaction row per price/volume pair
**Context:** A single `Operazione` block can list several `<price> EUR <volume>` fills for the same
instrument/day/venue.
**Decision:** Emit one `transactions` row per (price, volume) pair, sharing the operation's
instrument/nature/date/venue. `signal_value` = `price*volume` as a STORED generated column.
**Reasoning:** Makes `min_value`/signal filtering operate on real per-fill notional, keeps the
generated column a trivial expression, and loses no information.
