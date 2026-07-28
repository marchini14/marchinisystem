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

// Broj i raspored parova koje se skenira/trguje — HOT_PAIRS_COUNT parova s
// najvećim 24h prometom (shared/bitget.js: getHotSymbols), svježe dohvaćeno
// na svaki start servisa (ne fiksni popis). Veći broj parova nužno znači
// manji MAX_CAPITAL_USD po paru i mora ići na rjeđi raspored da LLM pozivi
// ne probiju besplatne dnevne limite (OpenRouter/Groq).
const HOT_PAIRS_COUNT = parseInt(process.env.HOT_PAIRS_COUNT || '3', 10);
const CANDLE_INTERVAL = process.env.CANDLE_INTERVAL || '15m';
const AGENT_CRON = process.env.AGENT_CRON || (HOT_PAIRS_COUNT > 10 ? '*/15 * * * *' : '*/5 * * * *');
// Svi agenti na isti cron tick bi inače pucali potpuno paralelno i probili
// rate-limit i na Bitgetu i na LLM provideru (viđeno uživo: 100 agenata
// odjednom = masovni "Too Many Requests" i nijedan uspješan ciklus). Umjesto
// toga procesiramo ih u malim serijama s pauzom između.
const AGENT_BATCH_SIZE = parseInt(process.env.AGENT_BATCH_SIZE || '5', 10);
const AGENT_BATCH_DELAY_MS = parseInt(process.env.AGENT_BATCH_DELAY_MS || '2000', 10);

let AGENTS = []; // popunjava se u start() nakon dohvata vrućih parova

app.get('/health', (_, res) => res.json({
  status: 'ok',
  ts: new Date().toISOString(),
  agents: AGENTS.length,
  liveTrading: risk.LIVE_TRADING,
}));

async function runAgent(agent) {
  try { await agent.execute(); }
  catch (err) { console.error(`[Scheduler] ${agent.name} failed:`, err.message); }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function runAllAgentsThrottled() {
  for (let i = 0; i < AGENTS.length; i += AGENT_BATCH_SIZE) {
    const batch = AGENTS.slice(i, i + AGENT_BATCH_SIZE);
    await Promise.allSettled(batch.map(([agent]) => runAgent(agent)));
    if (i + AGENT_BATCH_SIZE < AGENTS.length) await sleep(AGENT_BATCH_DELAY_MS);
  }
}

function scheduleAgents() {
  cron.schedule(AGENT_CRON, () => runAllAgentsThrottled());
  console.log(`[Scheduler] ${AGENTS.length} agenata na rasporedu ${AGENT_CRON}, serije po ${AGENT_BATCH_SIZE} uz ${AGENT_BATCH_DELAY_MS}ms pauze`);
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

  console.log(`[Startup] Dohvaćam ${HOT_PAIRS_COUNT} najprometnijih USDT-FUTURES parova...`);
  const symbols = await bitget.getHotSymbols(HOT_PAIRS_COUNT);
  console.log(`[Startup] Parovi: ${symbols.join(', ')}`);

  const capitalShareUsd = risk.MAX_CAPITAL_USD / symbols.length;
  AGENTS = symbols.map((symbol) => [
    new TradingAgent(`${symbol}-Trader`, { symbol, interval: CANDLE_INTERVAL, capitalShareUsd, leverage: risk.MAX_LEVERAGE }),
    AGENT_CRON,
  ]);

  // Run each agent once on startup so dashboard isn't empty
  console.log('[Startup] Running all agents once...');
  await runAllAgentsThrottled();
  console.log('[Startup] Initial run complete');

  scheduleAgents();

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`[Dashboard] http://0.0.0.0:${PORT}`);
  });
}

start().catch(err => {
  console.error('[Fatal]', err.message);
  process.exit(1);
});
