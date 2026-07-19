const BaseAgent = require('./base');
const { logResult } = require('../shared/redis');

class MendeleevAgent extends BaseAgent {
  constructor() { super('Mendeleev', 'Token composition'); }

  async run() {
    const strategies = [
      'Equal-weight DeFi basket', 'Momentum top-5 L1s',
      'Low-volatility stablecoin blend', 'LSD index rebalance',
      'AI & infra token basket', 'RWA portfolio composition'
    ];
    const strategy = this.pick(strategies);
    const tokens = this.randInt(4, 12);
    const aum = this.randInt(50000, 2000000);
    const rebalanceCost = this.rand(10, 200);
    const performancePct = this.rand(-3, 15);
    const amount = Math.max(0, Math.round(aum * (performancePct / 100) - rebalanceCost) * 100) / 100;
    await logResult(this.name, `Rebalanced: ${strategy} (${tokens} tokens)`, amount, 'USD',
      { strategy, tokens, aum, rebalanceCost, performancePct });
    return { strategy, tokens, aum, performancePct, amount };
  }
}

module.exports = MendeleevAgent;
