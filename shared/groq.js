const { chatDecision } = require('./llm-common');

const GROQ_API_URL = 'https://api.groq.com/openai/v1/chat/completions';
// Provjeri trenutno dostupne free-tier modele na console.groq.com/docs/models
// prije produkcije — ovo je razuman default, ne garancija da postoji zauvijek.
const MODEL = process.env.GROQ_MODEL || 'llama-3.3-70b-versatile';

// GROQ_API_KEY može biti jedan ključ ili više njih odvojenih zarezom — svaki
// Groq free-tier ključ ima svoj vlastiti dnevni rate-limit, pa rotacija po
// pozivu (i automatski prelazak na sljedeći ključ kod 429) efektivno množi
// raspoloživi dnevni limit brojem ključeva.
let rotationIndex = 0;

function getKeys() {
  return (process.env.GROQ_API_KEY || '')
    .split(',')
    .map((k) => k.trim())
    .filter(Boolean);
}

async function decide(symbol, marketData) {
  const keys = getKeys();
  if (keys.length === 0) throw new Error('GROQ_API_KEY nije postavljen');

  let lastErr;
  for (let i = 0; i < keys.length; i++) {
    const apiKey = keys[rotationIndex % keys.length];
    rotationIndex++;
    try {
      return await chatDecision({ url: GROQ_API_URL, apiKey, model: MODEL, symbol, marketData });
    } catch (err) {
      lastErr = err;
      if (err.status !== 429) throw err;
      // 429 (rate limit) na ovom ključu — pokušaj sljedeći prije odustajanja.
    }
  }
  throw lastErr;
}

module.exports = { decide, MODEL };
