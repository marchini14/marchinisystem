const { redis } = require('./redis');
const quant = require('./quant');

// LIVE_TRADING mora biti eksplicitno 'true' da bi bilo koji agent smio poslati
// stvarni nalog na Bitget. Bez toga agenti rade u dry-run modu: dohvaćaju
// prave tržišne podatke i pitaju LLM za odluku, ali ništa ne šalju na burzu.
const LIVE_TRADING = process.env.LIVE_TRADING === 'true';

const MAX_CAPITAL_USD = parseFloat(process.env.MAX_CAPITAL_USD || '150');
const MAX_LEVERAGE = parseFloat(process.env.MAX_LEVERAGE || '3');
const DAILY_LOSS_LIMIT_PCT = parseFloat(process.env.DAILY_LOSS_LIMIT_PCT || '10');

const KILLSWITCH_KEY = 'risk:killswitch';
const HALT_REASON_KEY = 'risk:halt_reason';

const TRADE_HISTORY_KEY = 'risk:trade_history';
const TRADE_HISTORY_MAX = 200;
const CONSECUTIVE_LOSSES_KEY = 'risk:consecutive_losses';
const MAX_CONSECUTIVE_LOSSES = parseInt(process.env.MAX_CONSECUTIVE_LOSSES || '4', 10);

// Kelly needs a minimum sample before its estimate means anything; below
// that, callers should fall back to flat equity-share sizing.
const KELLY_MIN_TRADES = parseInt(process.env.KELLY_MIN_TRADES || '10', 10);
const KELLY_LOOKBACK_TRADES = parseInt(process.env.KELLY_LOOKBACK_TRADES || '50', 10);
// Half-Kelly is standard practice — full Kelly sizing is usually too volatile
// for a real account. Also hard-cap the fraction regardless of what the
// formula suggests on a lucky/unlucky streak.
const KELLY_DAMPING = parseFloat(process.env.KELLY_DAMPING || '0.5');
const KELLY_FRACTION_CAP = parseFloat(process.env.KELLY_FRACTION_CAP || '0.5');

function todayKey() {
  return `risk:daily_pnl:${new Date().toISOString().slice(0, 10)}`;
}

async function isHalted() {
  return (await redis.get(KILLSWITCH_KEY)) === '1';
}

async function haltTrading(reason) {
  await redis.set(KILLSWITCH_KEY, '1');
  await redis.set(HALT_REASON_KEY, reason);
}

// Ručni reset — nitko ne smije programatski ukloniti kill-switch osim čovjeka.
// Briše i brojač uzastopnih gubitaka: bez toga bi jedan idući gubitak odmah
// ponovno okinuo cool-down umjesto da operater stvarno dobije čisti početak.
async function resetKillSwitch() {
  await redis.del(KILLSWITCH_KEY);
  await redis.del(HALT_REASON_KEY);
  await redis.del(CONSECUTIVE_LOSSES_KEY);
}

async function getHaltReason() {
  return redis.get(HALT_REASON_KEY);
}

// Poziva se nakon svakog zatvaranja pozicije s realiziranim P&L u USD.
// Kad dnevni gubitak pređe DAILY_LOSS_LIMIT_PCT od MAX_CAPITAL_USD, aktivira
// kill-switch i sve agente zaustavlja dok netko ručno ne pozove resetKillSwitch().
async function recordPnl(amountUsd) {
  const key = todayKey();
  const total = parseFloat(await redis.incrbyfloat(key, amountUsd));
  await redis.expire(key, 172800);

  const lossLimit = MAX_CAPITAL_USD * (DAILY_LOSS_LIMIT_PCT / 100);
  if (total <= -lossLimit) {
    await haltTrading(`Dnevni limit gubitka dosegnut: ${total.toFixed(2)} USD <= -${lossLimit.toFixed(2)} USD`);
  }
  return total;
}

// Poziva se nakon svakog zatvaranja pozicije. Vodi stvarnu povijest tradeova
// (za Kelly sizing) i broji uzastopne gubitke — nakon MAX_CONSECUTIVE_LOSSES
// zaredom aktivira isti kill-switch kao dnevni limit gubitka.
async function recordTradeOutcome(symbol, realizedPnlUsd) {
  const entry = JSON.stringify({
    symbol,
    pnl: realizedPnlUsd,
    win: realizedPnlUsd > 0,
    ts: new Date().toISOString(),
  });
  await redis.lpush(TRADE_HISTORY_KEY, entry);
  await redis.ltrim(TRADE_HISTORY_KEY, 0, TRADE_HISTORY_MAX - 1);

  await recordPnl(realizedPnlUsd);

  if (realizedPnlUsd > 0) {
    await redis.set(CONSECUTIVE_LOSSES_KEY, '0');
    return;
  }

  const streak = parseInt(await redis.incr(CONSECUTIVE_LOSSES_KEY), 10);
  if (streak >= MAX_CONSECUTIVE_LOSSES) {
    await haltTrading(`Cool-down: ${streak} uzastopnih gubitaka (limit ${MAX_CONSECUTIVE_LOSSES})`);
  }
}

// Usklađuje stvarno zatvorene pozicije s dnevnim risk limitom / Kelly
// poviješću. Nužno je proći kroz OVU funkciju, ne kroz agentov vlastiti
// "closed" put — jer se pozicija može zatvoriti i automatski (burzin
// stop-loss/take-profit bracket nalog), a bot za to inače ne bi ni znao pa se
// takav gubitak nikad ne bi ubrojio u dnevni limit gubitka. Dedup po
// pozicijeId (Bitgetov 'positionId') preko Redis SET NX osigurava da se svaki
// trade broji točno jednom bez obzira koliko se puta reconcile pozove za isti
// vremenski prozor.
async function reconcileClosedPositions(closedPositions) {
  for (const p of closedPositions) {
    const seenKey = `risk:seen_position:${p.positionId}`;
    const wasNew = await redis.set(seenKey, '1', 'EX', 7 * 24 * 3600, 'NX');
    if (wasNew !== 'OK') continue; // već ubrojeno u prethodnom ciklusu
    await recordTradeOutcome(p.symbol, parseFloat(p.netProfit || '0'));
  }
}

// Kelly udio kapitala izračunat iz stvarne (ne pretpostavljene) povijesti
// tradeova. Vraća null dok nema dovoljno uzorka (poziva capQty da onda
// koristi punu ravnu alokaciju umjesto nagađanja na premalo podataka).
async function getKellyFraction() {
  const raw = await redis.lrange(TRADE_HISTORY_KEY, 0, KELLY_LOOKBACK_TRADES - 1);
  const trades = raw.map((r) => JSON.parse(r));
  if (trades.length < KELLY_MIN_TRADES) return null;

  const wins = trades.filter((t) => t.win);
  const losses = trades.filter((t) => !t.win);
  if (wins.length === 0 || losses.length === 0) return null;

  const winProb = wins.length / trades.length;
  const avgWin = wins.reduce((a, t) => a + t.pnl, 0) / wins.length;
  const avgLoss = Math.abs(losses.reduce((a, t) => a + t.pnl, 0) / losses.length);
  if (avgLoss === 0) return null;

  const kelly = quant.kellyCriterion(winProb, avgWin / avgLoss);
  const damped = kelly * KELLY_DAMPING;
  return Math.max(0, Math.min(damped, KELLY_FRACTION_CAP));
}

// Veličina pozicije ograničena dodijeljenim udjelom kapitala i MAX_LEVERAGE
// hard-capom. kellyFraction (0-1, ili null) skalira koliko od equityShareUsd
// se stvarno koristi na temelju stvarne povijesti tradeova — null/nedovoljno
// podataka znači puna ravna alokacija (isto ponašanje kao prije).
function capQty({ equityShareUsd, price, leverage, kellyFraction = null }) {
  const lev = Math.min(Number(leverage) || 1, MAX_LEVERAGE);
  const allocatedUsd = kellyFraction === null ? equityShareUsd : equityShareUsd * kellyFraction;
  const notionalUsd = allocatedUsd * lev;
  return notionalUsd / price;
}

module.exports = {
  LIVE_TRADING,
  MAX_CAPITAL_USD,
  MAX_LEVERAGE,
  DAILY_LOSS_LIMIT_PCT,
  MAX_CONSECUTIVE_LOSSES,
  KELLY_MIN_TRADES,
  isHalted,
  haltTrading,
  resetKillSwitch,
  getHaltReason,
  recordPnl,
  recordTradeOutcome,
  reconcileClosedPositions,
  getKellyFraction,
  capQty,
};
