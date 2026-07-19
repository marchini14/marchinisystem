const BaseAgent = require('./base');
const { logResult } = require('../shared/redis');

class SatoshiAgent extends BaseAgent {
  constructor() { super('Satoshi', 'Cross-chain arb'); }

  async run() {
    const chains = ['Ethereum', 'Arbitrum', 'Optimism', 'Base', 'Polygon', 'BNB Chain', 'Avalanche'];
    const tokens = ['USDC', 'USDT', 'ETH', 'WBTC', 'DAI'];
    const from = this.pick(chains);
    const to = this.pick(chains.filter(c => c !== from));
    const token = this.pick(tokens);
    const volume = this.randInt(5000, 200000);
    const spreadPct = this.rand(0.1, 0.6);
    const amount = Math.round(volume * (spreadPct / 100) * 100) / 100;
    await logResult(this.name, `Cross-chain ${token}: ${from} → ${to}`, amount, 'USD',
      { from, to, token, volume, spreadPct });
    return { from, to, token, volume, amount };
  }
}

module.exports = SatoshiAgent;
