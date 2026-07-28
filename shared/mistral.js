const { chatDecision } = require('./llm-common');

const MISTRAL_API_URL = 'https://api.mistral.ai/v1/chat/completions';
// Provjeri trenutno dostupne besplatne modele na console.mistral.ai prije
// produkcije — ovo je razuman default u trenutku pisanja, ne trajna garancija.
const MODEL = process.env.MISTRAL_MODEL || 'mistral-small-latest';

async function decide(symbol, marketData) {
  const apiKey = process.env.MISTRAL_API_KEY;
  if (!apiKey) throw new Error('MISTRAL_API_KEY nije postavljen');
  return chatDecision({ url: MISTRAL_API_URL, apiKey, model: MODEL, symbol, marketData });
}

module.exports = { decide, MODEL };
