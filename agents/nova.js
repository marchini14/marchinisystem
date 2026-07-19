const BaseAgent = require('./base');
const { logResult } = require('../shared/redis');

class NovaAgent extends BaseAgent {
  constructor() { super('Nova', 'Airdrop hunter'); }

  async run() {
    const protocols = [
      'Arbitrum', 'Optimism', 'zkSync', 'Starknet',
      'LayerZero', 'Wormhole', 'Hop Protocol'
    ];
    const protocol = this.pick(protocols);
    const tokens = this.randInt(100, 10000);
    const pricePerToken = this.rand(0.05, 8);
    const amount = Math.round(tokens * pricePerToken * 100) / 100;
    await logResult(this.name, `Airdrop claimed: ${protocol} (${tokens} tokens)`, amount, 'USD',
      { protocol, tokens, pricePerToken });
    return { protocol, tokens, pricePerToken, amount };
  }
}

module.exports = NovaAgent;
