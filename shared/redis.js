const Redis = require('ioredis');

const redis = new Redis(
  process.env.REDIS_URL || process.env.REDIS_MASTER_URL || 'redis://localhost:6379',
  { maxRetriesPerRequest: 3, lazyConnect: false }
);

redis.on('error', (err) => console.error('[Redis] error:', err.message));
redis.on('connect', () => console.log('[Redis] connected'));

const RESULTS_KEY = 'agent:log';
const RESULTS_MAX = 200;

async function logResult(agentName, action, amount, currency = 'USD', metadata = {}) {
  const entry = JSON.stringify({
    id: Date.now(),
    agent_name: agentName,
    action,
    amount: parseFloat(amount),
    currency,
    metadata,
    created_at: new Date().toISOString()
  });

  const summaryKey = `agent:summary:${agentName}`;

  await Promise.all([
    redis.lpush(RESULTS_KEY, entry).then(() => redis.ltrim(RESULTS_KEY, 0, RESULTS_MAX - 1)),
    redis.hincrbyfloat(summaryKey, 'total_amount', parseFloat(amount)),
    redis.hincrby(summaryKey, 'total_runs', 1),
    redis.hset(summaryKey, 'last_run', new Date().toISOString())
  ]);
}

async function getResults(limit = 50) {
  const raw = await redis.lrange(RESULTS_KEY, 0, limit - 1);
  return raw.map(r => JSON.parse(r));
}

async function getAgentSummary() {
  const keys = await redis.keys('agent:summary:*');
  if (!keys.length) return [];

  const results = await Promise.all(
    keys.map(async (key) => {
      const data = await redis.hgetall(key);
      return {
        agent_name: key.replace('agent:summary:', ''),
        total_runs: parseInt(data.total_runs || 0),
        total_amount: parseFloat(data.total_amount || 0),
        last_run: data.last_run || null
      };
    })
  );

  return results.sort((a, b) => b.total_amount - a.total_amount);
}

module.exports = { redis, logResult, getResults, getAgentSummary };
