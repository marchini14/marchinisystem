const { RestClientV3 } = require('bitget-api');

const CATEGORY = 'USDT-FUTURES';

let client = null;
function getClient() {
  if (client) return client;
  const apiKey = process.env.BITGET_API_KEY;
  const apiSecret = process.env.BITGET_API_SECRET;
  const apiPass = process.env.BITGET_API_PASSPHRASE;
  if (!apiKey || !apiSecret || !apiPass) {
    throw new Error('BITGET_API_KEY / BITGET_API_SECRET / BITGET_API_PASSPHRASE nisu postavljeni');
  }
  client = new RestClientV3({ apiKey, apiSecret, apiPass });
  return client;
}

const instrumentCache = new Map();

// Dohvaća minOrderQty/quantityPrecision sa burze umjesto nagađanja,
// da se izbjegnu odbijeni nalozi zbog krive preciznosti.
async function getInstrument(symbol) {
  if (instrumentCache.has(symbol)) return instrumentCache.get(symbol);
  const res = await getClient().getInstruments({ category: CATEGORY, symbol });
  const info = res.data?.[0];
  if (!info) throw new Error(`Nema instrument podataka za ${symbol}`);
  instrumentCache.set(symbol, info);
  return info;
}

function roundQty(qty, quantityPrecision) {
  const decimals = Number(quantityPrecision) || 0;
  const factor = 10 ** decimals;
  return Math.floor(qty * factor) / factor;
}

// Bitget rejects trigger prices that aren't an exact multiple of the
// instrument's price tick size (error 45115: "price should be a multiple of
// X") — round-to-nearest here, unlike qty's floor (there's no "stay under
// budget" concern for a price).
function roundPrice(price, pricePrecision) {
  const decimals = Number(pricePrecision) || 0;
  const factor = 10 ** decimals;
  return Math.round(price * factor) / factor;
}

// "Vrući" parovi = najveći 24h promet (turnover24h, u USDT) — standardna
// definicija najlikvidnijih/najaktivnijih parova, ne nagađanje.
async function getHotSymbols(count) {
  const res = await getClient().getTickers({ category: CATEGORY });
  return res.data
    .filter((t) => t.symbol.endsWith('USDT') && parseFloat(t.turnover24h) > 0)
    .sort((a, b) => parseFloat(b.turnover24h) - parseFloat(a.turnover24h))
    .slice(0, count)
    .map((t) => t.symbol);
}

async function getTicker(symbol) {
  const res = await getClient().getTickers({ category: CATEGORY, symbol });
  const t = res.data?.[0];
  if (!t) throw new Error(`Nema tickera za ${symbol}`);
  return t;
}

// Vraća niz svijeća [ts, open, high, low, close, baseVol, quoteVol], najnovija zadnja.
async function getCandles(symbol, interval, limit = 50) {
  const res = await getClient().getCandles({ category: CATEGORY, symbol, interval, limit: String(limit) });
  return res.data;
}

async function getUsdtEquity() {
  const res = await getClient().getBalances();
  return parseFloat(res.data?.usdtEquity || '0');
}

// Bitget vraća { list: [...] }, ne direktni niz — res.data?.[0] bi uvijek bio
// undefined i agent nikad ne bi vidio postojeću poziciju (otvarao bi nove
// naloge umjesto da zatvori/okrene postojeći). U hedge_mode računu isti
// simbol teoretski može imati i long i short slot odvojeno; filtriramo na
// stvarno otvorene (total > 0) i vraćamo prvu — normalan tok (zatvori pa tek
// onda otvori suprotni smjer, nikad oboje isti ciklus) sprječava da ih ikad
// bude više od jedne otvorene.
async function getPosition(symbol) {
  const res = await getClient().getCurrentPosition({ category: CATEGORY, symbol });
  const positions = (res.data?.list || []).filter((p) => parseFloat(p.total) > 0);
  return positions[0] || null;
}

// marginMode nije dokumentiran parametar za set-leverage (POST
// /api/v3/account/set-leverage) u Bitgetovoj stvarnoj API referenci — SDK-ov
// TS tip ga navodi, ali burza ga odbija (400 Bad Request). Bez njega koristi
// se account-level default margin mode.
async function setLeverage(symbol, leverage, posSide) {
  return getClient().setLeverage({
    category: CATEGORY,
    symbol,
    leverage: String(leverage),
    posSide,
  });
}

// stopLoss/takeProfit su apsolutne cijene (ne postoci) — agent ih računa iz odluke.
async function placeMarketOrder({ symbol, side, posSide, qty, stopLossPrice, takeProfitPrice }) {
  const instrument = await getInstrument(symbol);
  const roundedQty = roundQty(qty, instrument.quantityPrecision);
  const minQty = parseFloat(instrument.minOrderQty || '0');
  if (roundedQty < minQty) {
    throw new Error(`Izračunata količina ${roundedQty} manja je od minOrderQty ${minQty} za ${symbol}`);
  }
  const roundedStopLoss = stopLossPrice ? roundPrice(stopLossPrice, instrument.pricePrecision) : undefined;
  const roundedTakeProfit = takeProfitPrice ? roundPrice(takeProfitPrice, instrument.pricePrecision) : undefined;

  // marginMode omitted: not a documented place-order parameter (see setLeverage).
  return getClient().submitNewOrder({
    category: CATEGORY,
    symbol,
    side,
    posSide,
    orderType: 'market',
    qty: String(roundedQty),
    reduceOnly: 'no',
    stopLoss: roundedStopLoss !== undefined ? String(roundedStopLoss) : undefined,
    takeProfit: roundedTakeProfit !== undefined ? String(roundedTakeProfit) : undefined,
    slOrderType: roundedStopLoss !== undefined ? 'market' : undefined,
    tpOrderType: roundedTakeProfit !== undefined ? 'market' : undefined,
  });
}

async function closePosition(symbol, posSide) {
  return getClient().closeAllPositions({ category: CATEGORY, symbol, posSide });
}

module.exports = {
  CATEGORY,
  getInstrument,
  getHotSymbols,
  getTicker,
  getCandles,
  getUsdtEquity,
  getPosition,
  setLeverage,
  placeMarketOrder,
  closePosition,
};
