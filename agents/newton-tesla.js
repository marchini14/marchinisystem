const BaseAgent = require('./base');
const { logResult } = require('../shared/redis');

class NewtonTeslaAgent extends BaseAgent {
  constructor() { super('Newton-Tesla', 'Flash loan arb'); }

  async run() {
    const pairs = ['ETH/USDC', 'BTC/ETH', 'SOL/USDC', 'ARB/ETH', 'OP/USDC', 'MATIC/ETH'];
    const dexes = ['Uniswap', 'Curve', 'Balancer', 'DODO', 'Camelot'];
    const pair = this.pick(pairs);
    const dexA = this.pick(dexes);
    const dexB = this.pick(dexes.filter(d => d !== dexA));
    const loanSize = this.randInt(10000, 500000);
    const profitPct = this.rand(0.05, 0.8);
    const amount = Math.round(loanSize * (profitPct / 100) * 100) / 100;
    await logResult(this.name, `Flash arb ${pair}: ${dexA} → ${dexB}`, amount, 'USD',
      { pair, dexA, dexB, loanSize, profitPct });
    return { pair, dexA, dexB, loanSize, amount };
  }
}

module.exports = NewtonTeslaAgent;
