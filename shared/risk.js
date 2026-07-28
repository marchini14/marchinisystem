const { redis } = require('./redis');

// LIVE_TRADING mora biti eksplicitno 'true' da bi bilo koji agent smio poslati
// stvarni nalog na Bitget. Bez toga agenti rade u dry-run modu: dohvaćaju
// prave tržišne podatke i pitaju LLM za odluku, ali ništa ne šalju na burzu.
const LIVE_TRADING = process.env.LIVE_TRADING === 'true';

const MAX_CAPITAL_USD = parseFloat(process.env.MAX_CAPITAL_USD || '150');
const MAX_LEVERAGE = parseFloat(process.env.MAX_LEVERAGE || '3');
const DAILY_LOSS_LIMIT_PCT = parseFloat(process.env.DAILY_LOSS_LIMIT_PCT || '10');

const KILLSWITCH_KEY = 'risk:killswitch';
const HALT_REASON_KEY = 'risk:halt_reason';

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
async function resetKillSwitch() {
  await redis.del(KILLSWITCH_KEY);
  await redis.del(HALT_REASON_KEY);
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

// Veličina pozicije ograničena dodijeljenim udjelom kapitala i MAX_LEVERAGE hard-capom.
function capQty({ equityShareUsd, price, leverage }) {
  const lev = Math.min(Number(leverage) || 1, MAX_LEVERAGE);
  const notionalUsd = equityShareUsd * lev;
  return notionalUsd / price;
}

module.exports = {
  LIVE_TRADING,
  MAX_CAPITAL_USD,
  MAX_LEVERAGE,
  DAILY_LOSS_LIMIT_PCT,
  isHalted,
  haltTrading,
  resetKillSwitch,
  getHaltReason,
  recordPnl,
  capQty,
};
