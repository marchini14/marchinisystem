const BaseAgent = require('./base');
const { logResult } = require('../shared/redis');

class TuringAgent extends BaseAgent {
  constructor() { super('Turing', 'Galxe / QuestN'); }

  async run() {
    const platforms = ['Galxe', 'QuestN', 'Layer3', 'Crew3', 'Zealy'];
    const questTypes = ['Twitter Follow', 'Discord Join', 'On-chain Task', 'Quiz', 'Refer Friend'];
    const platform = this.pick(platforms);
    const quest = this.pick(questTypes);
    const completedQuests = this.randInt(3, 15);
    const pointsEarned = completedQuests * this.randInt(50, 500);
    const amount = this.rand(5, 200); // USD value of rewards
    await logResult(this.name, `Completed ${completedQuests} quests on ${platform} [${quest}]`, amount, 'USD',
      { platform, quest, completedQuests, pointsEarned });
    return { platform, quest, completedQuests, pointsEarned, amount };
  }
}

module.exports = TuringAgent;
