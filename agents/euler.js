const BaseAgent = require('./base');
const { logResult } = require('../shared/redis');

class EulerAgent extends BaseAgent {
  constructor() { super('Euler', 'Yield farming'); }

  async run() {
    const protocols = ['Aave V3', 'Compound V3', 'Morpho Blue', 'Spark', 'Pendle', 'Convex', 'Yearn V3'];
    const assets = ['USDC', 'DAI', 'USDT', 'WETH', 'wstETH', 'rETH'];
    const protocol = this.pick(protocols);
    const asset = this.pick(assets);
    const deposited = this.randInt(10000, 500000);
    const apyPct = this.rand(2, 18);
    const daysActive = this.randInt(1, 30);
    const amount = Math.round(deposited * (apyPct / 100) * (daysActive / 365) * 100) / 100;
    await logResult(this.name, `Yield on ${asset} in ${protocol} (${apyPct}% APY)`, amount, 'USD',
      { protocol, asset, deposited, apyPct, daysActive });
    return { protocol, asset, deposited, apyPct, amount };
  }
}

module.exports = EulerAgent;
