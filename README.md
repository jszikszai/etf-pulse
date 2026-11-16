# ETF Pulse

Statikus GitHub Pages dashboard egy virtuális, 2026-11-02-án indított ETF-portfólióhoz.

## Telepítés

1. Töltsd fel a ZIP **tartalmát** a `jszikszai/etf-pulse` repository gyökerébe, a rejtett `.github` mappával együtt.
2. GitHub → Settings → Pages → Build and deployment → Source: **GitHub Actions**.
3. Actions → Deploy dashboard → Run workflow (vagy várd meg a push utáni futást).
4. Nyisd meg: https://jszikszai.github.io/etf-pulse/

## Adatok

`data/history.json`: minden rekord `date`, `VHYL`, `VWCE`, `EURHUF`, `cashHUF` mezőket tartalmaz. Az árak EUR-ban, a készpénz HUF-ban értendő. A 2026-11-02-i bázist tilos módosítani. A hiányzó napokat ne töltsd ki becsléssel. A VHYL jogosultságot keletkeztető osztalékát készpénzként add hozzá, ne vásárolj belőle további ETF-egységet.

A fájlban szereplő 2026-11-06-i és 2026-11-09-i adatok a beszélgetés korábbi jelentéseiből származnak, nem újonnan ellenőrzött tőzsdei lekérdezésből. Az eltérő szolgáltatói záróárak miatt közzététel előtt célszerű újra ellenőrizni őket. November 3–5. nincs rögzítve, ezért a grafikon nem mutat ezekre kitalált értéket.

**Fontos:** ez a csomag automatikus GitHub Pages *publikálást* tartalmaz feltöltéskor, de automatikus piaci adatletöltést **nem**. A napi adatfrissítést külön, megbízható forrásellenőrzéssel kell megvalósítani.

## Változtathatatlan bázis

A `data/baseline.json` tartalmazza a 2026-11-02-i eredeti portfólió-definíciót. A pontos pénzügyi értékek karakterláncként vannak tárolva, így a tizedesjegyek nem vesznek el lebegőpontos átalakítás miatt.

A `scripts/validate-baseline.ps1` a fájl minden védett értékét a szkriptbe írt, független konstansokhoz hasonlítja. Emellett ellenőrzi, hogy a `data/history.json` pontosan egy 2026-11-02-i rekordot tartalmazzon az eredeti VHYL- és VWCE-árral, EUR/HUF árfolyammal és nulla osztalékkészpénzzel. Hiányzó vagy módosított bázis esetén a GitHub Pages telepítés leáll.

Helyi ellenőrzés Windows PowerShellből:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\validate-baseline.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-baseline.ps1
```

PowerShell 7 használatakor a `powershell` parancs helyett `pwsh` használható. A tesztek ideiglenes másolatokon igazolják, hogy az eredeti bázis átmegy, a védett értékek bármely módosítása és a bázisfájl hiánya pedig hibát okoz.

## Phase 1 market-data staging

The Python 3 collector retrieves and validates one selected date from these
official sources:

- VHYL unadjusted `Close`: Euronext Amsterdam historical CSV, ISIN
  `IE00B8GKDB10`, MIC `XAMS`, EUR.
- VWCE closing-auction candidate: Deutsche Boerse delayed post-trade archive,
  ISIN `IE00BK5BQT80`, venue `XETA`/`XETR`, EUR. Retail late trades are not
  treated as the close. Ambiguous auction selection is explicitly marked
  `unverified`.
- EUR/HUF: ECB series `EXR.D.HUF.EUR.SP00.A`.

The collector never writes `data/history.json`, `data/baseline.json`, or
`index.html`. Normal collection writes only `.staging/market-data.json`, which
is git-ignored and therefore is not included in the GitHub Pages deployment.
Staged values must not be published until exchange redistribution rights have
been clarified. Existing staged observation keys are never replaced: a repeat
collection fails and requires explicit review.

Dry-run a date without writing any file:

```powershell
python .\scripts\collect_market_data.py --date 2026-11-13 --dry-run
```

Stage a date locally:

```powershell
python .\scripts\collect_market_data.py --date 2026-11-13
```

Use a different staging path if needed:

```powershell
python .\scripts\collect_market_data.py --date 2026-11-13 --output .staging\november.json
```

Run the fixture-based test suite:

```powershell
python -m unittest discover -s tests -v
```

Missing market dates, HTTP 404 archives, exhausted temporary retries, and
validation failures are recorded as issues. The collector does not forward-fill
or invent values. Deutsche Boerse daily archives are generated after the
trading day and retained briefly, so a current-day archive can legitimately be
unavailable. The post-trade feed does not carry an explicit closing-auction
flag; verification is based on the official Xetra market phase, regular CLOB
records, and price consistency through the Trade-at-Close window.

### Promote one verified date to dashboard history

Promotion is a separate, explicit local step. It appends a date only when VHYL,
VWCE, and EUR/HUF are all present and verified in staging. Existing dates,
incomplete dates, unverified values, baseline/pre-baseline dates, and dates that
would require reordering old records are skipped. Existing history records are
never reserialized or replaced. The latest recorded `cashHUF` balance is carried
forward unchanged; market prices and exchange rates are never carried forward.

Validate the candidate without writing:

```powershell
python .\scripts\update_history.py --date 2026-11-13 --dry-run
```

Append the complete verified date:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\validate-baseline.ps1
python .\scripts\update_history.py --date 2026-11-13
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\validate-baseline.ps1
```

This command changes `data/history.json`, which is part of the public dashboard.
Do not commit, push, or deploy newly collected exchange prices until the relevant
redistribution rights have been confirmed.

### Daily inspection workflow

`.github/workflows/collect-market-data.yml` runs on weekdays at 08:00
Europe/Budapest. GitHub cron expressions use UTC, so the workflow schedules both
06:00 and 07:00 UTC and permits only the invocation whose Budapest local hour is
08. This covers daylight-saving transitions without changing the schedule.
Manual `workflow_dispatch` runs bypass the time gate and may optionally specify a
`through_date`.

Before collection, the workflow runs all Python tests and immutable baseline
validation. It checks missing weekdays after the latest history record in
chronological order. Partial or unverified dates remain absent and are reported
as historical gaps, while later complete verified dates may still be promoted.
Results and a readable summary are uploaded as a workflow run artifact for 14
days.

Automatic history publication is disabled by default. The collection job remains
read-only unless the repository Actions variable
`ENABLE_MARKET_DATA_PUBLICATION` is exactly `true`. When enabled, a separate job
uses the existing history updater to append complete dates, validates the
baseline again, and commits only `data/history.json`. If history does not change,
the workflow exits successfully without a commit.

Because a push made with `GITHUB_TOKEN` does not trigger another push workflow,
the collection workflow explicitly calls the existing reusable deployment
workflow with the new commit SHA. The deployment job receives only `contents:
read`, `pages: write`, and `id-token: write`; the history-update job alone receives
`contents: write`. Shared concurrency prevents overlapping automation runs, and
the token-generated push cannot create a deployment loop.

### Market-data publication gate

Keep `ENABLE_MARKET_DATA_PUBLICATION` unset or set to `false` until permission is
documented. Euronext states that delayed data is free for internal use, while
public display and redistribution can require an EMDA, fees, a fee waiver, and
prior approval. Deutsche Boerse grants free delayed-data use when it is not
commercialized, but its policies still govern onward dissemination. A public
GitHub Pages dashboard is outside clearly permitted internal use, so the project
does not assume publication rights.

To enable the end-to-end path safely:

1. Obtain written confirmation or the required licence/waiver from Euronext for
   public display of the VHYL closing price.
2. Confirm with Deutsche Boerse that this non-commercial public display of the
   derived VWCE closing-auction value is permitted and record any attribution or
   disclaimer requirements.
3. Resolve every reported historical trading-date gap with verified official
   data. Do not add estimates or adjusted prices.
4. In GitHub, open **Settings → Secrets and variables → Actions → Variables** and
   create `ENABLE_MARKET_DATA_PUBLICATION` with value `true`.
5. Run **Collect ETF market data** manually once and inspect its staging and
   promotion summaries before relying on the weekday schedule.

Relevant policies:

- Euronext market-data policies: https://www.euronext.com/en/data/real-time-data/pricing-specs-agreements/market-data-pricing-policies
- Euronext agreement types: https://www.euronext.com/en/data/pricing-specs-agreements/data-agreements-types
- Deutsche Boerse delayed data: https://www.mds.deutsche-boerse.com/mds-en/real-time-data/Delayed-data
- Deutsche Boerse agreements: https://www.mds.deutsche-boerse.com/mds-en/real-time-data/agreements
