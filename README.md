# marchinisystem

## Bitget futures trading (agent-swarm)

`server.js` pokreće nekoliko trading agenata (`agents/trading-agent.js`) koji
za BTCUSDT/ETHUSDT/SOLUSDT USDT-FUTURES na Bitgetu dohvaćaju stvarne tržišne
podatke, pitaju LLM (`shared/llm.js`: OpenRouter primarno, Groq automatski
fallback) za long/short/flat odluku, i (samo ako je eksplicitno uključeno)
izvršavaju je preko [`bitget-api`](https://github.com/tiagosiebler/bitget-api)
(UTA v3 REST klijent).

**Ovo je jedini dio repozitorija koji dira stvaran novac.** Ostali agenti u
`agents/zero/`, `agents/nova/`, `agents/dashboard/` su namjerno read-only
(bug bounty scanning, airdrop tracking, dashboard) i nikad ne šalju naloge ni
ne drže privatne ključeve za potpisivanje transakcija — trading agenti su
svjesno drugačija, rizičnija kategorija.

### Postavljanje

1. Kopiraj `.env.example` u `.env` i popuni `BITGET_API_KEY/SECRET/PASSPHRASE`
   (dozvola: Futures Trade, **bez** withdraw) i barem jedan LLM ključ
   (`OPENROUTER_API_KEY` i/ili `GROQ_API_KEY` — OpenRouter je primaran, Groq
   je fallback ako je i on postavljen).
2. Postavi `MAX_CAPITAL_USD`, `MAX_LEVERAGE`, `DAILY_LOSS_LIMIT_PCT` po svom
   apetitu za rizik — ovo su hard capovi, LLM ih ne može zaobići
   (`shared/risk.js`).
3. Dok `LIVE_TRADING` nije točno `true`, agenti rade u **dry-run** modu:
   prava tržišna analiza i prava LLM odluka idu u `agent:log` u Redisu, ali se
   ništa ne šalje na burzu. Provjeri par ciklusa u ovom modu prije nego upališ
   `LIVE_TRADING=true`.
4. Kill-switch: ako dnevni gubitak pređe `DAILY_LOSS_LIMIT_PCT`, **ili** ako se
   dogodi `MAX_CONSECUTIVE_LOSSES` gubitaka zaredom (cool-down), svi agenti se
   automatski zaustavljaju (Redis ključ `risk:killswitch`) dok se ručno ne
   pozove `resetKillSwitch()` iz `shared/risk.js` (to briše i brojač
   uzastopnih gubitaka, ne samo kill-switch).
5. Position sizing uči iz stvarne povijesti tradeova: dok ima manje od
   `KELLY_MIN_TRADES` zatvorenih tradeova u Redisu, agenti koriste punu ravnu
   alokaciju (`capitalShareUsd`). Nakon toga `shared/risk.js: getKellyFraction()`
   računa half-Kelly udio iz stvarnog win-ratea i avg win/loss (`shared/quant.js:
   kellyCriterion`), hard-capiran na `KELLY_FRACTION_CAP`.

### Rizik

Futures trgovanje s leverageom preko automatiziranog agenta može izgubiti
cijeli, pa i više od uloženog kapitala (likvidacija). LLM odluke nisu
garantirano profitabilne — ovo je eksperimentalan sustav, ne financijski
savjet. Drži `MAX_CAPITAL_USD` na iznos koji si spreman u potpunosti izgubiti.
