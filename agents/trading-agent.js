const BaseAgent = require('./base');
const { logResult } = require('../shared/redis');
const bitget = require('../shared/bitget');
const llm = require('../shared/llm');
const risk = require('../shared/risk');

const MIN_CONFIDENCE = 0.45;

// LLM treba dovoljno svijeća unatrag da procijeni putanju (trend), ne samo
// zadnjih par — 12h pokriva i kratkoročni šum i stvarni smjer kretanja.
const LOOKBACK_HOURS = 12;

function intervalToMinutes(interval) {
  const match = /^(\d+)([mHhDd])$/.exec(interval);
  if (!match) throw new Error(`Nepoznat format intervala: ${interval}`);
  const n = Number(match[1]);
  const unit = match[2].toLowerCase();
  if (unit === 'm') return n;
  if (unit === 'h') return n * 60;
  if (unit === 'd') return n * 60 * 24;
  throw new Error(`Nepoznata jedinica intervala: ${match[2]}`);
}

// Generički futures trading agent: dohvaća stvarne tržišne podatke s Bitgeta,
// pita LLM (OpenRouter primarno, Groq fallback — shared/llm.js) za odluku
// (long/short/flat) i izvršava je unutar limita iz shared/risk.js. Dok
// LIVE_TRADING !== 'true' radi samo dry-run — nikad ne šalje stvaran nalog.
class TradingAgent extends BaseAgent {
  constructor(name, { symbol, interval, capitalShareUsd, leverage }) {
    super(name, `Bitget USDT-FUTURES trading — ${symbol} (${interval})`);
    this.symbol = symbol;
    this.interval = interval;
    this.capitalShareUsd = capitalShareUsd;
    this.leverage = leverage;
    this.candleLimit = Math.ceil((LOOKBACK_HOURS * 60) / intervalToMinutes(interval));
  }

  async fetchMarketSnapshot() {
    const [ticker, candles] = await Promise.all([
      bitget.getTicker(this.symbol),
      bitget.getCandles(this.symbol, this.interval, this.candleLimit),
    ]);
    return { ticker, candles };
  }

  async run() {
    if (!risk.LIVE_TRADING) {
      return this.runDryRun();
    }

    if (await risk.isHalted()) {
      const reason = await risk.getHaltReason();
      this.log('HALTED', { reason });
      await logResult(this.name, `Zaustavljen (kill-switch): ${reason}`, 0, 'USD', { halted: true });
      return { halted: true };
    }

    const { ticker, candles } = await this.fetchMarketSnapshot();
    const price = parseFloat(ticker.lastPrice);
    const existingPosition = await bitget.getPosition(this.symbol);

    const decision = await llm.decide(this.symbol, {
      price,
      change24hPct: ticker.price24hPcnt,
      recentCandles: candles,
      existingPosition: existingPosition
        ? {
            posSide: existingPosition.posSide,
            avgPrice: existingPosition.avgPrice,
            unrealisedPnl: existingPosition.unrealisedPnl,
          }
        : null,
    });

    const hasPosition = existingPosition && parseFloat(existingPosition.total) > 0;
    const wantsFlat = decision.action === 'flat' || decision.confidence < MIN_CONFIDENCE;

    if (hasPosition && (wantsFlat || decision.action !== existingPosition.posSide)) {
      await bitget.closePosition(this.symbol, existingPosition.posSide);
      // Stvaran realizirani P&L (i risk-limit/Kelly knjiženje) dolazi iz
      // server.js: risk.reconcileClosedPositions() protiv Bitgetove stvarne
      // povijesti — ne iz unrealisedPnl ovdje (samo procjena u trenutku
      // odluke, i ne pokriva pozicije zatvorene burzinim SL/TP nalogom).
      const estimatedPnl = parseFloat(existingPosition.unrealisedPnl || '0');
      await logResult(this.name, `Zatvorena ${existingPosition.posSide} pozicija na ${this.symbol}`, estimatedPnl, 'USD', { decision });
      return { closed: true, estimatedPnl, decision };
    }

    if (hasPosition) {
      this.log('HOLD', { symbol: this.symbol, posSide: existingPosition.posSide, decision });
      return { held: true, decision };
    }

    if (wantsFlat) {
      this.log('SKIP', { symbol: this.symbol, decision });
      return { skipped: true, decision };
    }

    const effectiveLeverage = await bitget.setLeverage(this.symbol, this.leverage, decision.action);
    const kellyFraction = await risk.getKellyFraction();
    const qty = risk.capQty({
      equityShareUsd: this.capitalShareUsd,
      price,
      leverage: effectiveLeverage,
      kellyFraction,
    });
    const stopLossPrice =
      decision.action === 'long'
        ? price * (1 - decision.stopLossPct / 100)
        : price * (1 + decision.stopLossPct / 100);
    const takeProfitPrice =
      decision.action === 'long'
        ? price * (1 + decision.takeProfitPct / 100)
        : price * (1 - decision.takeProfitPct / 100);

    const order = await bitget.placeMarketOrder({
      symbol: this.symbol,
      side: decision.action === 'long' ? 'buy' : 'sell',
      posSide: decision.action,
      qty,
      stopLossPrice,
      takeProfitPrice,
    });

    await logResult(this.name, `Otvorena ${decision.action} pozicija na ${this.symbol} @ ${price}`, 0, 'USD', {
      decision,
      qty,
      kellyFraction,
      stopLossPrice,
      takeProfitPrice,
      orderId: order.data?.orderId,
    });
    return { opened: true, decision, qty };
  }

  // Dry-run: stvarni tržišni podaci + stvarna LLM odluka, ali bez slanja naloga.
  async runDryRun() {
    const { ticker, candles } = await this.fetchMarketSnapshot();
    const price = parseFloat(ticker.lastPrice);
    const decision = await llm.decide(this.symbol, {
      price,
      change24hPct: ticker.price24hPcnt,
      recentCandles: candles,
      existingPosition: null,
    });
    await logResult(this.name, `[DRY-RUN] ${this.symbol} @ ${price} → ${decision.action}`, 0, 'USD', { decision, dryRun: true });
    return { dryRun: true, decision };
  }
}

module.exports = TradingAgent;
