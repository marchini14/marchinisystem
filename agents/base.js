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
      // bitget-api's BaseRestClient throws a plain object ({ code, message,
      // body, headers }) on non-2xx responses, not a real Error — err.message
      // there is just the generic HTTP status text (e.g. "Bad Request"),
      // while err.body has Bitget's actual validation error. Capture both.
      this.log('ERROR', { error: err.message || String(err), body: err.body });
    }
  }
}

module.exports = BaseAgent;
