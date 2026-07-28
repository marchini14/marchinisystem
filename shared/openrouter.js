const { chatDecision } = require('./llm-common');

const OPENROUTER_API_URL = 'https://openrouter.ai/api/v1/chat/completions';
// Provjeri trenutno dostupne besplatne modele (":free" sufiks) na
// https://openrouter.ai/models?max_price=0 prije produkcije — popis se često
// mijenja, ovo je razuman default u trenutku pisanja, ne trajna garancija.
const MODEL = process.env.OPENROUTER_MODEL || 'nvidia/nemotron-3-ultra-550b-a55b:free';

async function decide(symbol, marketData) {
  const apiKey = process.env.OPENROUTER_API_KEY;
  if (!apiKey) throw new Error('OPENROUTER_API_KEY nije postavljen');
  return chatDecision({
    url: OPENROUTER_API_URL,
    apiKey,
    model: MODEL,
    symbol,
    marketData,
    // OpenRouter preporučuje ove headere za rangiranje na openrouter.ai/rankings,
    // nisu strogo obavezni za rad API-ja.
    extraHeaders: {
      'HTTP-Referer': process.env.OPENROUTER_SITE_URL || 'https://github.com/marchini14/marchinisystem',
      'X-Title': 'marchinisystem-trading-agent',
    },
  });
}

module.exports = { decide, MODEL };
