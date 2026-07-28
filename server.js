const express = require('express');
const path = require('path');
const cron = require('node-cron');
const { redis, getResults, getAgentSummary } = require('./shared/redis');
const risk = require('./shared/risk');

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

// Kapital iz shared/risk.js (MAX_CAPITAL_USD) ravnomjerno podijeljen po agentu.
const SYMBOLS = [
  { symbol: 'BTCUSDT', interval: '15m', name: 'Btc-Trader' },
  { symbol: 'ETHUSDT', interval: '30m', name: 'Eth-Trader' },
  { symbol: 'SOLUSDT', interval: '1H',  name: 'Sol-Trader' },
];
const capitalShareUsd = risk.MAX_CAPITAL_USD / SYMBOLS.length;

// Agent registry: [agent, cron-expression]
const AGENTS = SYMBOLS.map(({ symbol, interval, name }) => [
  new TradingAgent(name, { symbol, interval, capitalShareUsd, leverage: risk.MAX_LEVERAGE }),
  '*/5 * * * *', // svakih 5 min
]);

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

function scheduleAgents() {
  for (const [agent, schedule] of AGENTS) {
    cron.schedule(schedule, () => runAgent(agent));
    console.log(`[Scheduler] ${agent.name.padEnd(15)} → ${schedule}`);
  }
  console.log(`[Scheduler] ${AGENTS.length} agents scheduled`);
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

  // Run each agent once on startup so dashboard isn't empty
  console.log('[Startup] Running all agents once...');
  await Promise.allSettled(AGENTS.map(([agent]) => runAgent(agent)));
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
