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

3. **Every body `<p>` MUST carry `style="padding:0 3.5rem;"`** - uniform
   left + right inset matching the rest of the drafting library. Apply to
   the recipient/sender/subject block, every numbered paragraph, the
   demand and consequences paragraphs, and the closing salutation. Do NOT
   apply this padding to paragraphs that are already centered (e.g.
   `BY REGISTERED POST A.D.` or the optional `WITHOUT PREJUDICE` banner).

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

7. **NO `---` horizontal rules anywhere.** The notice reads as a single
   continuous block on the advocate's letterhead. Section breaks are
   signalled by paragraph numbering and the centered banner lines (e.g.
   `BY REGISTERED POST A.D.`).

8. **NO PRAYER. NO VERIFICATION.** Notices are not court filings; they end
   with the advocate's signature block (and optional Copy-to / Enclosures
   / Mode of Service blocks). Do NOT borrow the centered + underlined
   PRAYER / VERIFICATION headings used by court filings.

9. **NO sub-numbering** like `1.1`, `1.2`, `2.1`. Single flat numbering 1,
   2, 3, ... throughout the body.

10. **NO data tables for facts.** Use prose. The only HTML tables
    permitted are: (a) a borderless 2-column "particulars" table for
    cheque bounce / eviction arrears computation when the agent's
    specialised prompt explicitly calls for one, and (b) the borderless
    advocate stationery banner if used. Data tables (1px borders)
    otherwise belong to court filings, not notices.

===== STATIONERY + RPAD HEADER (standard top of every notice) =====
Render the advocate stationery and dispatch banner verbatim, in this
order, at the very top of the body:

  <p style="text-align:center;margin:0.25rem 0;line-height:1.375;"><strong>[Advocate Full Name]</strong></p>
  <p style="text-align:center;margin:0.25rem 0;line-height:1.375;">[Advocate Credentials e.g. B.A., LL.B.]</p>
  <p style="text-align:center;margin:0.25rem 0;line-height:1.375;">[Office Address Line 1]</p>
  <p style="text-align:center;margin:0.25rem 0;line-height:1.375;">[Office Address Line 2 (City - Pincode)]</p>
  <p style="text-align:center;margin:0.25rem 0;line-height:1.375;">[Mob.: NNNNNNNNNN]   [Email: ...]   [Enrl. No.: ...]</p>

  <p style="text-align:center;margin:0.5rem 0;"><strong><u>BY REGISTERED POST A.D. / SPEED POST</u></strong></p>

  <p style="padding:0 3.5rem;"><strong>Dated: DD/MM/YYYY</strong></p>

===== RECIPIENT / SENDER / SUBJECT BLOCK =====
Each line a standalone `<p>` with `padding:0 3.5rem;`. Use `<strong>` for
the recipient and sender names, the SUBJECT label, and the inline statute
reference inside the SUBJECT line.

  <p style="padding:0 3.5rem;"><strong>To,</strong></p>
  <p style="padding:0 3.5rem;"><strong>1. [Recipient 1 Full Name]</strong></p>
  <p style="padding:0 3.5rem;">[Designation / Relationship if any]</p>
  <p style="padding:0 3.5rem;">[Address Line 1]</p>
  <p style="padding:0 3.5rem;">[Address Line 2 (City, State - Pincode)]</p>
  <p style="padding:0 3.5rem;">[Mob.: NNNNNNNNNN]</p>
  (Repeat the block, numbered 2, 3, ..., for additional addressees.)

  <p style="padding:0 3.5rem;"><strong>From,</strong></p>
  <p style="padding:0 3.5rem;"><strong>[Client Full Name]</strong></p>
  <p style="padding:0 3.5rem;">[Age / Occupation]</p>
  <p style="padding:0 3.5rem;">[Full Address]</p>
  <p style="padding:0 3.5rem;">Through: <strong>[Advocate Name]</strong>, Advocate</p>

  <p style="padding:0 3.5rem;"><strong>SUBJECT: [precise notice purpose, including the statute / cause and the property / amount / cheque it concerns].</strong></p>

  <p style="padding:0 3.5rem;">Dear Sir / Madam,</p>

  <p style="padding:0 3.5rem;">Under instructions from and on behalf of my client, <strong>[Client Full Name]</strong>, [age / occupation / address], hereinafter referred to as <strong>"my client"</strong>, I, <strong>[Advocate Name]</strong>, Advocate, do hereby serve upon you this Legal Notice and call upon you as follows:</p>

After this opener, begin the numbered body (paragraphs 1, 2, 3, ...).

===== SIGNATURE BLOCK (end of every notice) =====
Stacked plain `<p>` paragraphs. NO tables, NO 3-column layouts. The
typed-name pair sits at the bottom-right, the optional copy-to block
sits at the bottom-left below it. Emit verbatim:

  <p style="padding:0 3.5rem;margin-top:1.5rem;">Issued under my hand and seal at <strong>[City]</strong> on this <strong>DD/MM/YYYY</strong>.</p>

  <p style="padding:0 3.5rem;margin-top:0;">Yours faithfully,</p>

  <p style="text-align:right;margin:3.5rem 3.5rem 0;"><strong>[Advocate Name]</strong></p>
  <p style="text-align:right;margin:0 3.5rem;">Advocate for <strong>[Client Name]</strong></p>
  <p style="text-align:right;margin:0 3.5rem;">Enrl. No.: [Enrollment No. / State Bar Council]</p>

  <p style="padding:0 3.5rem;margin-top:1.5rem;"><strong>Copy to:</strong></p>
  <p style="padding:0 3.5rem;">1. My client - for information and record.</p>
  (Optional further copies: counsel-on-record, statutory authority, etc.)

  <p style="padding:0 3.5rem;margin-top:0.75rem;"><strong>Enclosures:</strong></p>
  <p style="padding:0 3.5rem;">[List supporting documents, numbered. If none: "Nil".]</p>

  <p style="padding:0 3.5rem;margin-top:0.75rem;"><strong>Mode of Service:</strong> By Registered Post Acknowledgement Due (R.P.A.D.) / Speed Post / Email / WhatsApp at the above-mentioned address(es).</p>
===== END NOTICE OUTPUT BASELINE =====
"""
