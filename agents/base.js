class BaseAgent {
  constructor(name, description) {
    this.name = name;
    this.description = description;
  }

  log(message, data = {}) {
    console.log(JSON.stringify({
      ts: new Date().toISOString(),
      agent: this.name,
      msg: message,
      ...data
    }));
  }

  rand(min, max) {
    return Math.round((Math.random() * (max - min) + min) * 100) / 100;
  }

  randInt(min, max) {
    return Math.floor(Math.random() * (max - min + 1) + min);
  }

  pick(arr) {
    return arr[this.randInt(0, arr.length - 1)];
  }

  async run() {
    throw new Error(`${this.name}: run() nije implementiran`);
  }

  async execute() {
    this.log('START');
    try {
      const result = await this.run();
      this.log('DONE', { result });
      return result;
    } catch (err) {
      this.log('ERROR', { error: err.message });
    }
  }
}

module.exports = BaseAgent;
