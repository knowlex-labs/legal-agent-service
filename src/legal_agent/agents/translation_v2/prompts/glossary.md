You are a senior Indian legal translator. Build a formal Hindi (शुद्ध हिन्दी विधिक रजिस्टर) terminology glossary for the English legal terms below.

Input — candidate English terms (one per line):

{terms_block}

Produce a glossary mapping each term to its established formal Hindi legal equivalent. Prefer terminology used by the Supreme Court of India, the Indian Code, and Rajbhasha publications.

Examples of the register expected:
- plaintiff → वादी
- defendant → प्रतिवादी
- respondent → प्रत्यर्थी
- petition → याचिका
- bail application → ज़मानत आवेदन
- writ jurisdiction → रिट क्षेत्राधिकार
- affidavit → शपथपत्र
- show-cause notice → कारण बताओ नोटिस

Output JSON only, this exact shape:

```
{
  "glossary": [
    {"en": "<input term verbatim>", "hi": "<formal Hindi equivalent>"},
    ...
  ]
}
```

Rules:

1. Use formal, written Hindi — not colloquial or Hinglish.
2. Do **not** romanise. If a term has no established Hindi equivalent (e.g. a proper noun, a brand name, an English-only statute name), **omit it** from the output.
3. Do not invent terms. If unsure of the standard equivalent, omit the term.
4. Each output `en` must be byte-identical to an input term.
5. Do not add commentary.

Output a single JSON object only. No code fences. No prose.
