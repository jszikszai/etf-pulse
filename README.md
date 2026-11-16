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
validation. It reads the Deutsche Boerse archive index and attempts only dates
newer than the latest history record for which an official daily Xetra archive
is still advertised. Results and a readable summary are uploaded as a workflow
run artifact for 14 days, not as GitHub Pages content. The workflow never runs
the history updater and
has read-only repository permissions, so it cannot change, commit, deploy, or
publish `data/history.json`.
