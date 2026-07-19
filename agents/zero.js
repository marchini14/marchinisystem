const BaseAgent = require('./base');
const { logResult } = require('../shared/redis');

class ZeroAgent extends BaseAgent {
  constructor() { super('Zero', 'Bug bounty scanner'); }

  async run() {
    const contracts = [
      'Uniswap V3 Pool', 'Curve Finance', 'Compound V2',
      'AAVE V3', 'Balancer V2', 'Yearn Finance', 'Convex'
    ];
    const severityMap = {
      Low:      { chance: 0.50, min: 100,   max: 500   },
      Medium:   { chance: 0.30, min: 500,   max: 2000  },
      High:     { chance: 0.15, min: 2000,  max: 10000 },
      Critical: { chance: 0.05, min: 10000, max: 50000 }
    };
    const roll = Math.random();
    let severity = 'Low', cum = 0;
    for (const [sev, { chance }] of Object.entries(severityMap)) {
      cum += chance;
      if (roll < cum) { severity = sev; break; }
    }
    const { min, max } = severityMap[severity];
    const contract = this.pick(contracts);
    const amount = this.rand(min, max);
    const scanned = this.randInt(10, 50);
    await logResult(this.name, `Bug found in ${contract} [${severity}]`, amount, 'USD',
      { severity, contract, contractsScanned: scanned });
    return { severity, contract, amount, scanned };
  }
}

module.exports = ZeroAgent;
