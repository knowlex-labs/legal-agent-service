<!--
STRUCTURAL REFERENCE for a General Legal Notice drafted on advocate's letterhead.

How to use this reference:
1. Substitute every `[Bracketed Field]` with values from STRUCTURED INPUT and
   REFERENCE DOCUMENTS CONTEXT.
2. A bracket survives in your final output ONLY when the value is absent from
   BOTH sources. Use clear, advocate-editable labels like `[Client Mobile]` or
   `[Statutory Provision]`. Never emit `[XX]`, `_____`, `XXXX`, `[NOT PROVIDED]`.
3. Do not invent values. Do not silently drop a line because data is missing —
   keep the line and bracket the missing field.
4. The stationery + RPAD banner + Dated line + recipient/sender/subject blocks
   are defined in the NOTICE BASELINE embedded in the system prompt. Start
   directly with the numbered body paragraphs below.
5. Use `<strong>` (NOT markdown `**`) for emphasis inside `<p>` tags.
-->

===== BODY PARAGRAPH SEQUENCE (general legal notice) =====

After the stationery / RPAD banner / dated line / recipient / sender /
SUBJECT / opener (all per the BASELINE block above), emit numbered `<p>`
paragraphs in this order. Each `<p>` carries
`style="padding:0 3.5rem;"`. Number consecutively 1, 2, 3, ...

**Paragraph 1 - Client identity and standing.**
Full name (in `<strong>`), age, occupation, residential / office
address, role in the matter (owner, employer, licensee, etc.), and any
documentary basis for that standing (registered sale deed, employment
contract, trademark registration, service agreement). One paragraph.

**Paragraph 2 onwards - Chronological factual narrative.**
One material event per paragraph, with specific dates (DD/MM/YYYY) and
specific amounts in figures + words (`Rs. 4,25,000/- (Rupees Four Lakh
Twenty-Five Thousand Only)`). Cover:
- The underlying transaction / relationship and how it began
- What the addressee did or failed to do (the wrong / breach / default)
- Communications already exchanged (prior emails, meetings, demands -
  with dates) and the addressee's responses or silence
- Continuing harm or escalation if any

**Middle paragraphs - Legal characterisation.**
A `<p>` paragraph stating my client's RIGHT and the legal basis for it
(e.g., "exclusive proprietorship of the registered trademark
'X' bearing No. ...... in Class 25, registered with the Trade Marks
Registry, Mumbai"; or "fundamental right under Article 19(1)(g) of the
Constitution"). Then a `<p>` paragraph characterising the addressee's
conduct under the relevant Indian statute(s):

  Inline pattern: "Your aforesaid acts constitute <strong>infringement of
  registered trademark</strong> under <strong>Section 29 of the Trade
  Marks Act, 1999</strong>, and amount to the tort of <strong>passing
  off</strong>; further, your conduct is also actionable as
  <strong>cheating</strong> under Section 318 of the Bharatiya Nyaya
  Sanhita, 2023 (corresponding to Section 420 of the Indian Penal Code,
  1860)."

Cite both old (IPC / CrPC) and new (BNS / BNSS / BSA) provisions where
relevant. Do NOT invent statutes. If unsure, leave a clearly-named
bracket `[Applicable Statutory Provision]`.

**Demand paragraph - "TAKE NOTICE".**
Inline `<strong>TAKE NOTICE</strong>` opener. List the specific actions
my client requires the addressee to take, with a precise time limit
(typically <strong>15 days</strong>; <strong>2 months</strong> for
Section 80 CPC). Use lettered sub-points `(a)`, `(b)`, `(c)` INSIDE the
same `<p>` separated by `;` semicolons - NOT as nested lists.

**Consequence paragraph - "TAKE FURTHER NOTICE".**
Inline `<strong>TAKE FURTHER NOTICE</strong>` opener. Spell out the
specific civil and / or criminal proceedings my client will initiate on
non-compliance:
  - Civil: suit for permanent injunction / specific performance /
    declaration / damages / mandatory injunction (name the court that
    would have jurisdiction)
  - Criminal: complaint under specific BNS / BNSS sections before the
    concerned Magistrate (or police if FIR-triggering offence)
  - Costs, damages, interest, and litigation expenses recoverable from
    the addressee

**Reservation-of-rights paragraph.**
A single `<p>`: this notice is issued without prejudice to all other
rights, remedies, and contentions of my client, all of which are
expressly reserved.

**Governing law and jurisdiction paragraph.**
A single `<p>` naming the court / tribunal / forum at <strong>[City]</strong>
that will have exclusive jurisdiction.

After the body, emit the SIGNATURE BLOCK and Copy-to / Enclosures /
Mode of Service blocks per the BASELINE.
===== END BODY PARAGRAPH SEQUENCE =====
