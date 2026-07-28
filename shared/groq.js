const GROQ_API_URL = 'https://api.groq.com/openai/v1/chat/completions';
// Provjeri trenutno dostupne free-tier modele na console.groq.com/docs/models
// prije produkcije — ovo je razuman default, ne garancija da postoji zauvijek.
const MODEL = process.env.GROQ_MODEL || 'llama-3.3-70b-versatile';

const SYSTEM_PROMPT = `Ti si oprezan asistent za crypto futures trgovanje. Dobit ćeš tržišne podatke (cijena, promjena 24h, nedavne svijeće, eventualna postojeća pozicija).
Odgovori ISKLJUČIVO JSON objektom s ovim poljima, bez dodatnog teksta:
{"action": "long" | "short" | "flat", "confidence": broj 0-1, "stopLossPct": broj (postotak od ulazne cijene, npr 1.5), "takeProfitPct": broj (postotak od ulazne cijene, npr 3), "reasoning": "kratko obrazloženje na hrvatskom"}.
Budi konzervativan — ako signal nije jasan ili je tržište kaotično, vrati "flat". Nikad ne predlaži ništa izvan ova četiri polja (npr. leverage, veličinu pozicije) — to određuje sustav upravljanja rizikom, ne ti.`;

async function decide(symbol, marketData) {
  const apiKey = process.env.GROQ_API_KEY;
  if (!apiKey) throw new Error('GROQ_API_KEY nije postavljen');

  const res = await fetch(GROQ_API_URL, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model: MODEL,
      temperature: 0.2,
      response_format: { type: 'json_object' },
      messages: [
        { role: 'system', content: SYSTEM_PROMPT },
        { role: 'user', content: JSON.stringify({ symbol, ...marketData }) },
      ],
    }),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Groq API greška ${res.status}: ${text}`);
  }

  const data = await res.json();
  const content = data.choices?.[0]?.message?.content;
  if (!content) throw new Error('Prazan odgovor od Groq API-ja');

  let decision;
  try {
    decision = JSON.parse(content);
  } catch {
    throw new Error(`Groq odgovor nije valjan JSON: ${content}`);
  }

  if (!['long', 'short', 'flat'].includes(decision.action)) decision.action = 'flat';
  decision.confidence = Math.max(0, Math.min(1, Number(decision.confidence) || 0));
  decision.stopLossPct = Math.max(0.2, Math.min(10, Number(decision.stopLossPct) || 1.5));
  decision.takeProfitPct = Math.max(0.2, Math.min(20, Number(decision.takeProfitPct) || 3));
  decision.reasoning = String(decision.reasoning || '').slice(0, 500);

  return decision;
}

module.exports = { decide, MODEL };
