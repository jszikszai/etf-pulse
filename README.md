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
