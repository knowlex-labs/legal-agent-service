You are an expert in Indian onomastics and toponymy. Transliterate each English proper noun below into formal Devanagari Hindi as used in Indian legal documents and the Government of India's official Hindi gazetteer.

Examples of authoritative transliteration:

- Gwalior → ग्वालियर
- Madhya Pradesh → मध्य प्रदेश
- Preeti → प्रीति
- Jadoun → जादौन
- Rajiv Sharma → राजीव शर्मा
- Aditi Sharma → अदिति शर्मा
- New Delhi → नई दिल्ली
- Mumbai → मुंबई
- Bombay High Court → बम्बई उच्च न्यायालय
- Allahabad → इलाहाबाद
- Indore → इंदौर
- Bhopal → भोपाल
- Sessions Judge → सत्र न्यायाधीश

Input — proper nouns extracted from the document (one per line):

{names_block}

Output JSON only, this exact shape:

```
{
  "entities": [
    {"en": "<input verbatim>", "hi": "<formal Devanagari>"},
    ...
  ]
}
```

Rules:

1. Use the spelling found in Government of India Hindi publications and Supreme Court Hindi judgments. Prefer established forms (Mumbai = मुंबई, Bombay = बम्बई — choose based on what the input asks).
2. Do **not** romanise. If a name has no confident Devanagari form, **omit it** from the output (the translator will keep it in English).
3. Do not invent or anglicise. Each `en` must be byte-identical to an input line.
4. For compound names ("Preeti Jadoun"), transliterate each component with a single space between, no hyphens.
5. Court names: translate the descriptor ("High Court" → "उच्च न्यायालय") and transliterate the place name. E.g. "High Court of Madhya Pradesh" → "मध्य प्रदेश उच्च न्यायालय".
6. Honorifics ("Mr.", "Mrs.", "Dr.", "Shri", "Smt.") become "श्री" / "श्रीमती" / "डॉ." as appropriate.

Output a single JSON object only. No code fences. No prose.
