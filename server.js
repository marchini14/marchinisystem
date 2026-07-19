const express = require('express');
const path = require('path');
const cron = require('node-cron');
const { redis, getResults, getAgentSummary } = require('./shared/redis');

// Agents
const ZeroAgent        = require('./agents/zero');
const NovaAgent        = require('./agents/nova');
const NewtonTeslaAgent = require('./agents/newton-tesla');
const ZoraAgent        = require('./agents/zora');
const SatoshiAgent     = require('./agents/satoshi');
const TuringAgent      = require('./agents/turing');
const PlanckBohrAgent  = require('./agents/planck-bohr');
const ChronosAgent     = require('./agents/chronos');
const EulerAgent       = require('./agents/euler');
const MendeleevAgent   = require('./agents/mendeleev');

const PORT = process.env.PORT || 8080;
const app = express();
app.use(express.static(path.join(__dirname, 'dashboard/public')));

app.get('/api/summary', async (req, res) => {
  try { res.json(await getAgentSummary()); }
  catch (err) { res.status(500).json({ error: err.message }); }
});

app.get('/api/results', async (req, res) => {
  try { res.json(await getResults(50)); }
  catch (err) { res.status(500).json({ error: err.message }); }
});

app.get('/health', (_, res) => res.json({ status: 'ok', ts: new Date().toISOString(), agents: 10 }));

// Agent registry: [agent, cron-expression]
const AGENTS = [
  [new ZeroAgent(),        '*/30 * * * *'],    // svakih 30 min
  [new NovaAgent(),        '10 */1 * * *'],    // svaki sat u :10
  [new NewtonTeslaAgent(), '*/15 * * * *'],    // svakih 15 min
  [new ZoraAgent(),        '*/45 * * * *'],    // svakih 45 min
  [new SatoshiAgent(),     '*/20 * * * *'],    // svakih 20 min
  [new TuringAgent(),      '0 */2 * * *'],     // svakih 2 sata
  [new PlanckBohrAgent(),  '*/10 * * * *'],    // svakih 10 min
  [new ChronosAgent(),     '5 */1 * * *'],     // svaki sat u :05
  [new EulerAgent(),       '0 */4 * * *'],     // svakih 4 sata
  [new MendeleevAgent(),   '0 */3 * * *'],     // svakih 3 sata
];

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
