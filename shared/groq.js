const { chatDecision } = require('./llm-common');

const GROQ_API_URL = 'https://api.groq.com/openai/v1/chat/completions';
// Provjeri trenutno dostupne free-tier modele na console.groq.com/docs/models
// prije produkcije — ovo je razuman default, ne garancija da postoji zauvijek.
const MODEL = process.env.GROQ_MODEL || 'llama-3.3-70b-versatile';

async function decide(symbol, marketData) {
  const apiKey = process.env.GROQ_API_KEY;
  if (!apiKey) throw new Error('GROQ_API_KEY nije postavljen');
  return chatDecision({ url: GROQ_API_URL, apiKey, model: MODEL, symbol, marketData });
}

module.exports = { decide, MODEL };
