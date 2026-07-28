const openrouter = require('./openrouter');
const groq = require('./groq');

// OpenRouter je primarni provider (širi izbor besplatnih modela), Groq je
// fallback (stabilniji/predvidljiviji free tier limiti) — koristi se ako
// OpenRouter nije konfiguriran ili njegov poziv ne uspije.
async function decide(symbol, marketData) {
  const hasOpenRouter = !!process.env.OPENROUTER_API_KEY;
  const hasGroq = !!process.env.GROQ_API_KEY;

  if (!hasOpenRouter && !hasGroq) {
    throw new Error('Ni OPENROUTER_API_KEY ni GROQ_API_KEY nisu postavljeni');
  }

  if (hasOpenRouter) {
    try {
      const decision = await openrouter.decide(symbol, marketData);
      return { ...decision, provider: 'openrouter', model: openrouter.MODEL };
    } catch (err) {
      console.error('[LLM] OpenRouter nije uspio, prelazim na Groq fallback:', err.message);
      if (!hasGroq) throw err;
    }
  }

  const decision = await groq.decide(symbol, marketData);
  return { ...decision, provider: 'groq', model: groq.MODEL };
}

module.exports = { decide };
