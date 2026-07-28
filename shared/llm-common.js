const SYSTEM_PROMPT = `Ti si oprezan asistent za crypto futures trgovanje. Dobit ćeš tržišne podatke (cijena, promjena 24h, nedavne svijeće, eventualna postojeća pozicija).
Odgovori ISKLJUČIVO JSON objektom s ovim poljima, bez dodatnog teksta:
{"action": "long" | "short" | "flat", "confidence": broj 0-1, "stopLossPct": broj (postotak od ulazne cijene, npr 1.5), "takeProfitPct": broj (postotak od ulazne cijene, npr 3), "reasoning": "kratko obrazloženje na hrvatskom"}.
Budi konzervativan — ako signal nije jasan ili je tržište kaotično, vrati "flat". Nikad ne predlaži ništa izvan ova četiri polja (npr. leverage, veličinu pozicije) — to određuje sustav upravljanja rizikom, ne ti.`;

function normalizeDecision(raw) {
  const decision = { ...raw };
  if (!['long', 'short', 'flat'].includes(decision.action)) decision.action = 'flat';
  decision.confidence = Math.max(0, Math.min(1, Number(decision.confidence) || 0));
  decision.stopLossPct = Math.max(0.2, Math.min(10, Number(decision.stopLossPct) || 1.5));
  decision.takeProfitPct = Math.max(0.2, Math.min(20, Number(decision.takeProfitPct) || 3));
  decision.reasoning = String(decision.reasoning || '').slice(0, 500);
  return decision;
}

// Zajednička logika za bilo koji OpenAI-kompatibilan chat completions API
// (Groq i OpenRouter oba prate taj format).
async function chatDecision({ url, apiKey, model, symbol, marketData, extraHeaders = {} }) {
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
      ...extraHeaders,
    },
    body: JSON.stringify({
      model,
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
    const err = new Error(`${url} greška ${res.status}: ${text}`);
    err.status = res.status;
    throw err;
  }

  const data = await res.json();
  const content = data.choices?.[0]?.message?.content;
  if (!content) throw new Error(`Prazan odgovor sa ${url}`);

  let decision;
  try {
    decision = JSON.parse(content);
  } catch {
    throw new Error(`Odgovor nije valjan JSON: ${content}`);
  }

  return normalizeDecision(decision);
}

module.exports = { chatDecision, normalizeDecision, SYSTEM_PROMPT };
