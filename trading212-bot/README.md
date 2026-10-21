# Trading-212-Trading-Bot

Ein selbstständig handelnder Aktien-Bot für die [Trading 212 Public API](https://docs.trading212.com/api).
Er wählt US-Aktien anhand von **technischer Analyse** und **aktuellen Nachrichten** aus (optional mit
KI-Bewertung durch Claude), kauft nur bei positivem Gesamtbild und verwaltet jede Position mit
festen Regeln für **Stop-Loss, Gewinnmitnahme, Trailing-Stop und Zeit-Stop**.

> ⚠️ **Wichtiger Hinweis:** Kein Bot kann Gewinne garantieren. Märkte sind unvorhersehbar, und auch
> eine gute Strategie hat Verlustphasen. Verluste bis hin zum Totalverlust des eingesetzten Kapitals
> sind möglich. Teste den Bot mehrere Wochen im **Demo-Konto**, bevor du echtes Geld einsetzt, und
> setze nur Geld ein, dessen Verlust du verkraften kannst. Dies ist keine Anlageberatung.

## So arbeitet der Bot

```
Watchlist (US-Aktien)
   │
   ├─ 1. Filter "mittleres Risiko": Volatilität 22–70 % p. a., Mindestumsatz, Mindestkurs
   ├─ 2. Technischer Score: Trend (50/200-Tage-Linie), Momentum (1 und 3 Monate), RSI,
   │     Volumenanstieg, Nähe zum 52-Wochen-Hoch
   ├─ 3. Nachrichten der letzten Tage (Finnhub oder Yahoo) für die technisch besten 12 Werte
   ├─ 4. Nachrichtenbewertung: Claude (KI) oder Schlagwort-Analyse
   │     → Sentiment, Stärke des Katalysators, Warnsignale (Betrug, Ermittlungen, Verwässerung …)
   ├─ 5. Gesamt-Score = 55 % Technik + 45 % News; Kauf nur bei Score ≥ Schwelle,
   │     positiver Nachrichtenlage, ohne Warnsignale und nicht überkauft
   └─ 6. Positionsgröße nach Risiko: max. 1 % des Depots Verlust bis zum Stop-Loss,
         max. 12 % des Depots pro Aktie, 10 % Cash-Reserve
```

**Ausstiegsregeln je Position** (alle 5 Minuten geprüft):

| Regel | Standard |
|---|---|
| Stop-Loss | Einstieg − 2 × ATR (Average True Range) |
| Teilgewinn | bei +3R (3-faches Risiko) wird die Hälfte verkauft, Rest auf Einstand abgesichert |
| Trailing-Stop | ab +1R: Höchstkurs seit Einstieg − 2,5 × ATR – lässt Gewinner laufen |
| Zeit-Stop | nach 20 Tagen mit weniger als +2 % wird verkauft (Kapital freimachen) |
| Negative News | neue Warnsignale oder stark negative Nachrichten nach dem Einstieg → Verkauf |

**Schutzmechanismen:** Tagesverlustlimit (−3 % → keine neuen Käufe), max. 8 Positionen,
max. 2 neue Käufe pro Durchlauf, 3 Tage Sperre für Wiedereinstieg, Handel nur zu US-Börsenzeiten
(nicht in den ersten 5 und letzten 10 Minuten), Echtgeld-Modus nur mit ausdrücklicher Bestätigung.
Vom Bot nicht gekaufte (manuelle) Positionen werden **nicht** angefasst.

Die Trading-212-API erlaubt im Echtgeld-Konto nur Market-Orders. Stops und Kursziele werden daher
vom Bot selbst überwacht – **er muss dafür laufen**. Ist der Bot aus, gibt es keinen Stop-Loss.

## Installation

Voraussetzung: Python 3.10 oder neuer.

```bash
cd trading212-bot
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # Zugangsdaten eintragen
cp config.example.yaml config.yaml
```

### Zugangsdaten (`.env`)

| Variable | Woher |
|---|---|
| `T212_API_KEY`, `T212_API_SECRET` | Trading-212-App → Einstellungen → API (Beta). Für Demo- und Echtgeld-Konto gibt es getrennte Schlüssel. Erlaube nur die nötigen Rechte (Konto lesen, Portfolio lesen, Orders lesen/ausführen, Metadaten) und schränke den Schlüssel möglichst auf deine IP ein. |
| `FINNHUB_API_KEY` | kostenlos auf [finnhub.io](https://finnhub.io/register) (für `news.provider: finnhub`) |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) (für `news.analyzer: claude`) |

Ohne Finnhub- bzw. Anthropic-Key: in der `config.yaml` `news.provider: yahoo` bzw.
`news.analyzer: lexicon` setzen. Die KI-Analyse ist deutlich treffsicherer als die
Schlagwort-Analyse, weil sie Erwartungen, Kontext und „bereits eingepreist“ berücksichtigt; sie
kostet pro Durchlauf einige API-Aufrufe (ca. 12 Kandidaten + gehaltene Positionen alle 30 Minuten).
Mit `claude_effort: low` werden die Kosten gesenkt.

## Benutzung

```bash
python -m t212bot status          # Kontostand und Positionen (inkl. Stop/Ziel des Bots)
python -m t212bot scan            # nur analysieren: Rangliste mit Begründung, keine Orders
python -m t212bot once            # ein kompletter Durchlauf (handelt, wenn Börse offen)
python -m t212bot once --dry-run  # ein Durchlauf ohne Orders
python -m t212bot run             # Dauerbetrieb
```

Beispielausgabe von `scan` (gekürzt, Werte fiktiv):

```
Symbol       Kurs  Vola  RSI     3M  Tech  News Gesamt  Entscheidung
----------------------------------------------------------------------------------------------------
AMD        245.29   31%   57   +18%  0.86  0.88   0.87  KAUF Kaufsignal (Score 0.87)
        News (6, claude): Sentiment +0.70, Katalysator 0.80 – Starke Zahlen, Prognose angehoben.
...
```

### Empfohlener Ablauf

1. `environment: demo` (Standard) – `scan` ausführen und die Begründungen prüfen.
2. Den Bot einige Wochen im Demo-Konto mit `run` laufen lassen.
3. `data/trades.csv` auswerten (jeder Kauf/Verkauf mit Grund und Ergebnis in %) und
   Parameter in `config.yaml` anpassen.
4. Erst dann Echtgeld: neue Live-API-Schlüssel in `.env`, `environment: live` und
   `T212_LIVE_CONFIRM=I_ACCEPT_THE_RISK` setzen. Mit kleinem Betrag beginnen.

### Dauerbetrieb

Der Bot muss während der US-Handelszeiten (15:30–22:00 Uhr deutscher Zeit) laufen, z. B. auf
einem kleinen Server/Raspberry Pi mit `tmux`, `screen` oder als systemd-Dienst:

```ini
# /etc/systemd/system/t212bot.service
[Service]
WorkingDirectory=/home/pi/trading212-bot
ExecStart=/home/pi/trading212-bot/.venv/bin/python -m t212bot run
Restart=always
```

Logs: `logs/bot.log` · Zustand: `data/state.json` · Journal: `data/trades.csv`

## Wichtige Parameter (`config.yaml`)

| Parameter | Wirkung |
|---|---|
| `strategy.buy_threshold` | höher = weniger, aber selektivere Käufe |
| `strategy.min_volatility` / `max_volatility` | Risikoband; höhere Volatilität = mehr Chance und mehr Risiko |
| `risk.risk_per_trade_pct` | Verlust pro Trade bis zum Stop (Standard 1 %) |
| `risk.max_position_pct` | maximale Gewichtung einer Aktie |
| `risk.take_profit_r_multiple` | Kursziel für den Teilverkauf |
| `risk.trailing_atr_multiple` | kleiner = Gewinne enger sichern, größer = Trends länger laufen lassen |
| `universe.watchlist` | welche Aktien überhaupt infrage kommen |

## Projektstruktur

```
t212bot/
  broker.py        Trading 212 API (Auth, Rate-Limits, Retries, Orders, Positionen)
  market_data.py   Kurshistorie und Wechselkurse (Yahoo Finance)
  news.py          Nachrichten (Finnhub / Yahoo)
  sentiment.py     Nachrichtenbewertung (Claude oder Schlagwörter)
  indicators.py    RSI, ATR, gleitende Durchschnitte, Volatilität
  scoring.py       Filter, Scores und Kaufentscheidung
  risk.py          Positionsgröße, Stop-Loss, Teilgewinn, Trailing-Stop, Zeit-Stop
  engine.py        Handelsablauf (Abgleich, Ausstiege, Suche, Käufe)
  state.py         gespeicherter Zustand und Trade-Journal
  universe.py      Zuordnung Symbol ↔ Trading-212-Ticker
  market_hours.py  US-Handelszeiten und Feiertage
tests/             Tests (laufen offline): pip install -r requirements-dev.txt && pytest
```

## Grenzen

- Nur US-Aktien aus der Watchlist; ETFs und andere Börsen werden nicht gehandelt.
- Kursdaten von Yahoo Finance sind inoffiziell und können zeitweise ausfallen – der Bot
  überspringt dann die betroffenen Werte.
- Die Feiertagsliste deckt 2026/2027 ab; spätere Jahre über `market_hours.holidays` ergänzen.
- Es gibt keinen Backtest der Nachrichtenkomponente (historische News-Bewertungen sind nicht
  verfügbar). Die Demo-Phase ersetzt ihn.
- Steuern, Gebühren für Währungsumrechnung (Trading 212: 0,15 %) und Spreads schmälern den Ertrag.
