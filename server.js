const express = require('express');
const path = require('path');
const cron = require('node-cron');
const { redis, getResults, getAgentSummary } = require('./shared/redis');
const risk = require('./shared/risk');
const bitget = require('./shared/bitget');

// Agents
const TradingAgent = require('./agents/trading-agent');

const PORT = process.env.PORT || 8080;
const app = express();

app.use((req, res, next) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET');
  next();
});

app.use(express.static(path.join(__dirname, 'dashboard/public')));

app.get('/api/summary', async (req, res) => {
  try { res.json(await getAgentSummary()); }
  catch (err) { res.status(500).json({ error: err.message }); }
});

app.get('/api/results', async (req, res) => {
  try { res.json(await getResults(50)); }
  catch (err) { res.status(500).json({ error: err.message }); }
});

// Dvoslojno skeniranje: HOT_PAIRS_COUNT parova s najvećim 24h prometom
// (shared/bitget.js: getHotTickers) se svaki ciklus BESPLATNO provjerava
// (samo ticker podaci, bez LLM poziva) i rangira po jačini 24h momentuma.
// LLM (i time stvaran trade) se poziva samo za top LLM_CANDIDATES_COUNT tog
// popisa — bez ovoga, 70-100 parova na LLM-u odmah probije besplatni dnevni
// budžet tokena (Groq TPD je po organizaciji, ne po ključu — viđeno uživo:
// 100k/dan potrošeno za par minuta na 70 istovremenih LLM poziva).
const HOT_PAIRS_COUNT = parseInt(process.env.HOT_PAIRS_COUNT || '70', 10);
const LLM_CANDIDATES_COUNT = parseInt(process.env.LLM_CANDIDATES_COUNT || '5', 10);
const CANDLE_INTERVAL = process.env.CANDLE_INTERVAL || '15m';
// Default raspored je namjerno rijedak (svaka 2h) — 5 LLM poziva x 12
// ciklusa/dan = 60 poziva/dan, sigurno ispod 100k TPD budžeta uz razumnu
// rezervu. Gušći raspored (npr. 15 min) zahtijeva manji LLM_CANDIDATES_COUNT
// ili plaćeni LLM tier da ne probije dnevni limit.
const AGENT_CRON = process.env.AGENT_CRON || '0 */2 * * *';
// Serije s pauzom umjesto svih odjednom — 100 paralelnih poziva je uživo
// izazvalo masovni "Too Many Requests" i na Bitgetu i na LLM provideru.
const AGENT_BATCH_SIZE = parseInt(process.env.AGENT_BATCH_SIZE || '3', 10);
const AGENT_BATCH_DELAY_MS = parseInt(process.env.AGENT_BATCH_DELAY_MS || '3000', 10);

let lastScan = { pool: [], shortlist: [] }; // za /health i dashboard

app.get('/health', (_, res) => res.json({
  status: 'ok',
  ts: new Date().toISOString(),
  scannedPairs: lastScan.pool.length,
  activePairs: lastScan.shortlist,
  liveTrading: risk.LIVE_TRADING,
}));

async function runAgent(agent) {
  try { await agent.execute(); }
  catch (err) { console.error(`[Scheduler] ${agent.name} failed:`, err.message); }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function runAgentsThrottled(agents) {
  for (let i = 0; i < agents.length; i += AGENT_BATCH_SIZE) {
    const batch = agents.slice(i, i + AGENT_BATCH_SIZE);
    await Promise.allSettled(batch.map((agent) => runAgent(agent)));
    if (i + AGENT_BATCH_SIZE < agents.length) await sleep(AGENT_BATCH_DELAY_MS);
  }
}

// Besplatan pred-filter: rangira cijeli skenirani pool po apsolutnoj 24h
// promjeni cijene (jačina momentuma) koristeći ticker podatke koje već
// imamo — bez ijednog dodatnog poziva na burzu ili LLM.
async function runScanCycle() {
  const pool = await bitget.getHotTickers(HOT_PAIRS_COUNT);
  const shortlist = pool
    .slice()
    .sort((a, b) => Math.abs(parseFloat(b.price24hPcnt)) - Math.abs(parseFloat(a.price24hPcnt)))
    .slice(0, LLM_CANDIDATES_COUNT)
    .map((t) => t.symbol);
  lastScan = { pool: pool.map((t) => t.symbol), shortlist };
  console.log(`[Scan] ${pool.length} parova skenirano, top ${shortlist.length} po momentumu: ${shortlist.join(', ')}`);

  const capitalShareUsd = risk.MAX_CAPITAL_USD / shortlist.length;
  const agents = shortlist.map((symbol) => new TradingAgent(`${symbol}-Trader`, {
    symbol,
    interval: CANDLE_INTERVAL,
    capitalShareUsd,
    leverage: risk.MAX_LEVERAGE,
  }));
  await runAgentsThrottled(agents);
}

async function start() {
  await redis.ping();
  console.log('[Redis] ping OK');

  console.log(`[Risk] LIVE_TRADING=${risk.LIVE_TRADING} MAX_CAPITAL_USD=${risk.MAX_CAPITAL_USD} MAX_LEVERAGE=${risk.MAX_LEVERAGE} DAILY_LOSS_LIMIT_PCT=${risk.DAILY_LOSS_LIMIT_PCT}`);
  if (!risk.LIVE_TRADING) {
    console.log('[Risk] LIVE_TRADING nije "true" — agenti rade u dry-run modu, ne šalju stvarne naloge.');
  }
  if (await risk.isHalted()) {
    console.log(`[Risk] KILL-SWITCH AKTIVAN: ${await risk.getHaltReason()}`);
  }

  // Run one scan+decide cycle on startup so dashboard isn't empty
  console.log('[Startup] Prvi scan+decide ciklus...');
  await runScanCycle();
  console.log('[Startup] Initial run complete');

  cron.schedule(AGENT_CRON, () => runScanCycle());
  console.log(`[Scheduler] Sken ${HOT_PAIRS_COUNT} parova / LLM na top ${LLM_CANDIDATES_COUNT}, raspored ${AGENT_CRON}`);

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`[Dashboard] http://0.0.0.0:${PORT}`);
  });
}

start().catch(err => {
  console.error('[Fatal]', err.message);
  process.exit(1);
});
