const openrouter = require('./openrouter');
const groq = require('./groq');
const mistral = require('./mistral');

// OpenRouter je primarni provider (širi izbor besplatnih modela), Groq je
// prvi fallback, Mistral drugi — svaki sljedeći se pokušava samo ako
// prethodni nije konfiguriran ili njegov poziv ne uspije.
async function decide(symbol, marketData) {
  const hasOpenRouter = !!process.env.OPENROUTER_API_KEY;
  const hasGroq = !!process.env.GROQ_API_KEY;
  const hasMistral = !!process.env.MISTRAL_API_KEY;

  if (!hasOpenRouter && !hasGroq && !hasMistral) {
    throw new Error('Nijedan LLM API ključ nije postavljen (OPENROUTER_API_KEY / GROQ_API_KEY / MISTRAL_API_KEY)');
  }

  if (hasOpenRouter) {
    try {
      const decision = await openrouter.decide(symbol, marketData);
      return { ...decision, provider: 'openrouter', model: openrouter.MODEL };
    } catch (err) {
      console.error('[LLM] OpenRouter nije uspio, prelazim na Groq fallback:', err.message);
    }
  }

  if (hasGroq) {
    try {
      const decision = await groq.decide(symbol, marketData);
      return { ...decision, provider: 'groq', model: groq.MODEL };
    } catch (err) {
      console.error('[LLM] Groq nije uspio, prelazim na Mistral fallback:', err.message);
      if (!hasMistral) throw err;
    }
  }

  const decision = await mistral.decide(symbol, marketData);
  return { ...decision, provider: 'mistral', model: mistral.MODEL };
}

module.exports = { decide };
