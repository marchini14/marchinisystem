const BaseAgent = require('./base');
const { logResult } = require('../shared/redis');

class ZoraAgent extends BaseAgent {
  constructor() { super('Zora', 'Free NFT mint'); }

  async run() {
    const collections = [
      'Zorb Genesis', 'Base Paint Daily', 'Highlight Drop',
      'Manifold Edition', 'Sound.xyz Drop', 'Mirror Essay NFT'
    ];
    const collection = this.pick(collections);
    const minted = this.randInt(1, 10);
    const floorPrice = this.rand(0.002, 0.15);
    const amount = Math.round(minted * floorPrice * 1800 * 100) / 100; // ETH → USD
    await logResult(this.name, `Minted ${minted}x ${collection}`, amount, 'USD',
      { collection, minted, floorPrice, ethPrice: 1800 });
    return { collection, minted, floorPrice, amount };
  }
}

module.exports = ZoraAgent;
