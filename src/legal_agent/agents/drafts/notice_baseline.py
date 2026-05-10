"""Shared HTML conventions for the four notice drafting agents."""

NOTICE_BASELINE_BLOCK = """
===== NOTICE OUTPUT BASELINE (READ BEFORE DRAFTING) =====
This block governs HOW the notice is rendered. Substantive content and
section ordering are defined later in your specialised prompt. The rules
below are non-negotiable and override any conflicting habits from training
data or older few-shots.

1. **NO court cause title.** Notices are letters, not filings. Do NOT emit
   `IN THE HON'BLE …`, `Vs.`, party blocks, or `MCRC No.`. The advocate
   stationery + recipient/sender block + SUBJECT line replace it entirely.

2. **Body is HTML `<p>` blocks - NOT markdown numbered lists, NOT `##`
   sections.** Each numbered paragraph is a standalone `<p>` element with
   the explicit number inside the paragraph text. `<ol><li>` parsing
   collapses on the editor edit-save round-trip; `<p>` blocks survive.

3. **Spacing is encoded by inline `style` margins**, not by blank lines.
   Use the EXACT margin tokens shown in the templates below. Block-internal
   lines (recipient address lines, sender address lines, signature stack)
   carry `margin:0;` so they sit tight together; block-START lines carry a
   top margin (e.g. `margin:1rem 0 0;`) so each block reads as a discrete
   group. Do NOT replace these inline margins with `<br>`, blank `<p>`, or
   markdown blank lines.

4. **Use `<strong>...</strong>` and `<em>...</em>` for emphasis - NEVER
   markdown `**bold**` or `*italic*`.** Markdown emphasis is not parsed
   inside HTML blocks; asterisks render literally. Bold party names,
   amounts in figures + words, statutory references, dates of demand, and
   inline openers like `<strong>TAKE NOTICE</strong>` /
   `<strong>TAKE FURTHER NOTICE</strong>`.

5. **NO em-dashes (`-` U+2014) or en-dashes (`-` U+2013) anywhere.**
   ASCII hyphen-minus only (`-`, U+002D). Applies to credentials, address
   lines, citations, prose, and section labels alike.

6. **NO `## ` / `### ` headings inside the notice body** for
   "INTRODUCTION", "FACTS", "LEGAL POSITION", "DEMAND", "CONSEQUENCES",
   etc. Categorical structure is conveyed by the numbered paragraphs
   themselves and inline `<strong>` openers within those paragraphs.

7. **NO `---` horizontal rules anywhere.** Section breaks are signalled
   by paragraph margins and the centered banner lines (e.g.
   `BY REGISTERED POST A.D.`).

8. **NO PRAYER. NO VERIFICATION.** Notices are not court filings; they end
   with the advocate's signature block and Copy-to / Enclosures / Mode of
   Service blocks.

9. **NO sub-numbering** like `1.1`, `1.2`, `2.1`. Single flat numbering 1,
   2, 3, ... throughout the body.

10. **NO data tables for facts.** Use prose. The only HTML tables permitted
    are: (a) a borderless 2-column "particulars" table for cheque bounce /
    eviction / demand arrears computation when the agent's specialised
    prompt explicitly calls for one. Data tables (1px borders) otherwise
    belong to court filings, not notices.

11. **SUBJECT IS A SINGLE LINE - 25 WORDS MAX.** It names the type of
    notice, the principal amount or cheque number or property, and ONE
    governing statute. It does NOT recite the demand, the consequences,
    the contractual interest rate, the case caption, or the full
    statutory framework. Those belong in the body.

    GOOD (one line, ~15 words):
      Demand Notice - Recovery of Rs. 8,42,000/- under Master Services
      Agreement dated 04/01/2025.

    BAD (multi-line, 50+ words, recites everything):
      Legal Demand Notice under Section 73 of the Indian Contract Act,
      1872, calling upon FastShop Retail Pvt. Ltd. to forthwith pay the
      outstanding sum of Rs. 8,00,000/- (Rupees Eight Lakh Only) together
      with contractual interest @ 18% per annum and notice costs, being
      dues arising under the Master Services Agreement dated 04/01/2025
      and Invoices No. GTC/2025/041 and GTC/2025/068.

===== STATIONERY + RPAD HEADER (top of every notice) =====
Render verbatim, in this order. Centered stationery + RPAD banner + the
"Dated:" line:

  <p style="text-align:center;margin:0.25rem 0;line-height:1.375;"><strong>[Advocate Full Name]</strong></p>
  <p style="text-align:center;margin:0;line-height:1.375;">[Advocate Credentials e.g. B.A., LL.B.]</p>
  <p style="text-align:center;margin:0;line-height:1.375;">[Office Address Line 1]</p>
  <p style="text-align:center;margin:0;line-height:1.375;">[Office Address Line 2 (City - Pincode)]</p>
  <p style="text-align:center;margin:0;line-height:1.375;">[Mob.: NNNNNNNNNN]   [Email: ...]   [Enrl. No.: ...]</p>

  <p style="text-align:center;margin:1.25rem 0 0.5rem;"><strong><u>BY REGISTERED POST A.D. / SPEED POST</u></strong></p>

  <p style="padding:0 3.5rem;margin:1rem 0 0;"><strong>Dated: DD/MM/YYYY</strong></p>

===== RECIPIENT / SENDER / SUBJECT BLOCK =====
Each block is a contiguous group of `<p>` tags. The FIRST line of each
group carries `margin:1rem 0 0;` so a clear gap separates blocks. Every
subsequent line in the SAME block carries `margin:0;` so address lines sit
tight. Number multiple recipients 1., 2., 3.

  <p style="padding:0 3.5rem;margin:1rem 0 0;"><strong>To,</strong></p>
  <p style="padding:0 3.5rem;margin:0;"><strong>1. [Recipient 1 Full Name]</strong></p>
  <p style="padding:0 3.5rem;margin:0;">[Designation / Relationship if any]</p>
  <p style="padding:0 3.5rem;margin:0;">[Address Line 1]</p>
  <p style="padding:0 3.5rem;margin:0;">[Address Line 2 (City, State - Pincode)]</p>
  <p style="padding:0 3.5rem;margin:0;">[Mob.: NNNNNNNNNN]</p>
  (Repeat the inner block, numbered 2., 3., ..., with `margin:0;` for each
  line; for the leading "2." name line use `margin:0.5rem 0 0;` to break
  it from recipient 1.)

  <p style="padding:0 3.5rem;margin:1rem 0 0;"><strong>From,</strong></p>
  <p style="padding:0 3.5rem;margin:0;"><strong>[Client Full Name]</strong></p>
  <p style="padding:0 3.5rem;margin:0;">[Age / Occupation]</p>
  <p style="padding:0 3.5rem;margin:0;">[Full Address]</p>
  <p style="padding:0 3.5rem;margin:0;">Through: <strong>[Advocate Name]</strong>, Advocate</p>

  <p style="padding:0 3.5rem;margin:1.25rem 0 0;"><strong>SUBJECT: [single-line subject, 25 words max].</strong></p>

  <p style="padding:0 3.5rem;margin:1rem 0 0;">Dear Sir / Madam,</p>

  <p style="padding:0 3.5rem;margin:0.75rem 0 0;">Under instructions from and on behalf of my client, <strong>[Client Full Name]</strong>, [age / occupation / address], hereinafter referred to as <strong>"my client"</strong>, I, <strong>[Advocate Name]</strong>, Advocate, do hereby serve upon you this Legal Notice and call upon you as follows:</p>

===== NUMBERED BODY PARAGRAPHS =====
Every numbered body paragraph carries:

  style="padding:0 3.5rem;margin:0.85rem 0 0;"

That is: 3.5rem horizontal inset (matches the rest of the drafting library)
and 0.85rem of TOP margin so successive paragraphs are visually separated
without feeling double-spaced. Bottom margin is 0; the next paragraph's
top margin owns the gap.

  <p style="padding:0 3.5rem;margin:0.85rem 0 0;">1. [substantive paragraph 1].</p>

  <p style="padding:0 3.5rem;margin:0.85rem 0 0;">2. [substantive paragraph 2].</p>

For the demand paragraph and consequences paragraph, the SAME margin
applies. The inline `<strong>TAKE NOTICE</strong>` /
`<strong>TAKE FURTHER NOTICE</strong>` opener stays inside the same
paragraph - do NOT promote it to a centered banner.

If a paragraph contains a borderless particulars TABLE (only allowed in
the demand / cheque bounce / eviction prompts), the intro sentence is
its own `<p>` with the standard margin; the `<table>` element follows
directly, then a closing `<p>` aggregating the total. The table itself
carries its own `margin:0.5rem auto;` and need not be wrapped.

===== SIGNATURE BLOCK (end of every notice) =====
Stacked plain `<p>` paragraphs. NO tables, NO 3-column layouts. The
typed-name pair sits at the bottom-right, the Copy-to / Enclosures /
Mode of Service blocks sit at the bottom-left below it. Emit verbatim:

  <p style="padding:0 3.5rem;margin:1.5rem 0 0;">Issued under my hand and seal at <strong>[City]</strong> on this <strong>DD/MM/YYYY</strong>.</p>

  <p style="padding:0 3.5rem;margin:0.5rem 0 0;">Yours faithfully,</p>

  <p style="text-align:right;margin:3.5rem 3.5rem 0;"><strong>[Advocate Name]</strong></p>
  <p style="text-align:right;margin:0 3.5rem;">Advocate for <strong>[Client Name]</strong></p>
  <p style="text-align:right;margin:0 3.5rem;">Enrl. No.: [Enrollment No. / State Bar Council]</p>

  <p style="padding:0 3.5rem;margin:1.5rem 0 0;"><strong>Copy to:</strong></p>
  <p style="padding:0 3.5rem;margin:0;">1. My client - for information and record.</p>
  (Optional further copies on additional `margin:0` lines.)

  <p style="padding:0 3.5rem;margin:0.75rem 0 0;"><strong>Enclosures:</strong></p>
  <p style="padding:0 3.5rem;margin:0;">[Numbered list of supporting documents, one per `margin:0` line. If none: "Nil".]</p>

  <p style="padding:0 3.5rem;margin:0.75rem 0 0;"><strong>Mode of Service:</strong> By Registered Post Acknowledgement Due (R.P.A.D.) / Speed Post / Email at the above-mentioned address(es).</p>
===== END NOTICE OUTPUT BASELINE =====
"""
