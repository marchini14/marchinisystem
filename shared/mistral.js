const { chatDecision } = require('./llm-common');

const MISTRAL_API_URL = 'https://api.mistral.ai/v1/chat/completions';
// Provjeri trenutno dostupne besplatne modele na console.mistral.ai prije
// produkcije — ovo je razuman default u trenutku pisanja, ne trajna garancija.
const MODEL = process.env.MISTRAL_MODEL || 'mistral-small-latest';

// MISTRAL_API_KEY može biti jedan ključ ili više njih odvojenih zarezom —
// isti round-robin + retry-na-429 obrazac kao shared/groq.js. Napomena: ako
// više ključeva pripada istoj Mistral organizaciji, ne mora nužno množiti
// dnevni budžet (vidi Groq TPD-po-organizaciji slučaj), ali svejedno pomaže
// kod kratkotrajnih per-minute burst limita.
let rotationIndex = 0;

function getKeys() {
  return (process.env.MISTRAL_API_KEY || '')
    .split(',')
    .map((k) => k.trim())
    .filter(Boolean);
}

async function decide(symbol, marketData) {
  const keys = getKeys();
  if (keys.length === 0) throw new Error('MISTRAL_API_KEY nije postavljen');

  let lastErr;
  for (let i = 0; i < keys.length; i++) {
    const apiKey = keys[rotationIndex % keys.length];
    rotationIndex++;
    try {
      return await chatDecision({ url: MISTRAL_API_URL, apiKey, model: MODEL, symbol, marketData });
    } catch (err) {
      lastErr = err;
      if (err.status !== 429) throw err;
    }
  }
  throw lastErr;
}

module.exports = { decide, MODEL };
