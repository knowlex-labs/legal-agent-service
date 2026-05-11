"""Shared HTML conventions for criminal-court drafting agents.

Mirrors `notice_baseline.py` for the criminal-filing family: anticipatory
bail, regular bail, quashing, criminal appeal, SLP, revision. Each agent
imports `COURT_FILING_BASELINE_BLOCK` and supplies only its own paragraph
sequence, statute references, grounds taxonomy, and CRITICAL NOTES.
"""

COURT_FILING_BASELINE_BLOCK = """
===== SUBSTITUTION CONTRACT (READ FIRST) =====
Every `[Bracketed Field]` in the templates below is a SUBSTITUTION SLOT, not
output text. Fill each slot using the user's STRUCTURED INPUT and REFERENCE
DOCUMENTS CONTEXT.

A bracket survives in your final output ONLY when the value is absent from
BOTH STRUCTURED INPUT and REFERENCE DOCUMENTS - and even then, write a
clear, advocate-editable label like `[Applicant Mobile]`, `[Crime Number]`,
`[FIR Date]`. Never emit `[XX]`, `_____`, `XXXX`, `[NOT PROVIDED]`,
`[Date]`, `[Amount]`, or guidance brackets like
`[Title - Shri/Smt/Kumari/Mr./Ms.]` (those were drafting hints; pick the
right honorific from the source data and emit it directly).

Do not invent values. Do not silently drop a line because the data is
missing - keep the line and bracket the missing field with a clear name.
===== END SUBSTITUTION CONTRACT =====

===== CAUSE TITLE - RENDERED SEPARATELY, DO NOT EMIT =====
The cause title (court banner `IN THE HON'BLE …`, `AT …`, the case caption
`MCRC No. … / [Year]` or `S.L.P. (Crl.) No. … / [Year]` or
`Crl. Appeal No. … / [Year]`, the petitioner / appellant / applicant + State
respondent party blocks, the centered `Vs.` separator, and the centered +
underlined document title e.g. `APPLICATION UNDER SECTION 438 CrPC /
SECTION 482 BNSS`) is rendered deterministically by the system and
PREPENDED to your output.

DO NOT emit any of those elements. DO NOT emit a `## CAUSE TITLE` heading,
a `# IN THE HON'BLE …` banner, the case caption line, or any party block at
the top of your draft. Start your output directly with the body opener
shown in your specialised prompt.
===== END CAUSE TITLE =====

===== BODY STRUCTURE =====
The body is a FLAT LIST OF NUMBERED PARAGRAPHS - Indian-court convention
for criminal pleadings.

**CRITICAL - EMIT EACH NUMBERED PARAGRAPH AS A PLAIN HTML `<p>` BLOCK.** Do
NOT use markdown numbered-list syntax (`1.`, `2.`, `3.` at line start with
blank lines between). Markdown list parsing collapses the structure on
edit-save round-trips, producing a wall of text with literal `**` markers.
Instead, write each paragraph as its own `<p>` element with the explicit
number inside the paragraph text:

  <p style="padding:0 3.5rem;">1. The applicant, <strong>[Full Name]</strong>,
  S/O <strong>[Father's Name]</strong>, aged about [XX] years, [Occupation], is
  a permanent resident of [Full Address] and has deep roots in the community.</p>

Use `<strong>...</strong>` and `<em>...</em>` for emphasis (party names,
key terms, statute references, dates, amounts). Do NOT use markdown
`**bold**` or `*italic*` inside body HTML - markdown emphasis is not parsed
inside HTML blocks and will render as literal asterisks.

**EVERY body `<p>` MUST include `style="padding:0 3.5rem;"`** - uniform
3.5rem padding on BOTH left AND right - so the numbered body sits inset
symmetrically from the page edges, matching standard Indian-court layout.
The opener `<p>` and every numbered paragraph carry this exact style. Do
NOT apply this padding to the centered PRAYER and VERIFICATION headings
(they are already centered).

Do NOT emit `##` or `###` section headings inside the body for "FACTS",
"GROUNDS", "STATUS OF PRIOR APPLICATIONS", "QUESTIONS OF LAW", "CASE
DETAILS", or any similar heading. The categorical structure is conveyed
by the numbered paragraphs themselves and inline `<strong>` openers (see
GROUNDS section below).

Do NOT use sub-numbering like `1.1`, `1.2`, `2.1` in the body. Single flat
numbering 1, 2, 3, … throughout.

Do NOT emit `---` horizontal rules anywhere in the document. Section
breaks are signalled only by the centered + bold + underlined PRAYER and
VERIFICATION headings.

**NO em-dashes (`-` U+2014) or en-dashes (`-` U+2013) anywhere.** ASCII
hyphen-minus only (`-`, U+002D). Applies to citations, addresses, prose,
category labels, signatures - everywhere.
===== END BODY STRUCTURE =====

===== CATEGORICAL GROUNDS PATTERN =====
Where your specialised prompt groups grounds into categories `(A)`, `(B)`,
`(C)`, …, the CATEGORY-OPENING paragraph (the first paragraph of each
group) does NOT carry a leading numeric prefix - just the bold Title Case
category label followed by a hyphen and the substance. The
`(A)`/`(B)`/`(C)` letter IS the marker for that paragraph; adding a number
alongside is redundant and looks wrong.

CONTINUATION paragraphs within the same category keep the flat numbering
(continuing from the last numbered paragraph). Numbering naturally "skips"
each category-opener position since openers have no number. The result
reads as: ... 6, 7, **(A) Title -** substance, 8, 9, 10, **(B) Title -**
substance, 11, 12, **(C) Title -** substance, 13, …

Example (Category opener - Title Case label, NO leading number):

  <p style="padding:0 3.5rem;"><strong>(A) Illegality of Conviction -</strong>
  The impugned judgment is illegal, perverse, and contrary to the evidence
  on record …</p>

Example (Continuation paragraph in category A - flat-numbered, picks up
where the body numbering left off before the (A) opener):

  <p style="padding:0 3.5rem;">8. The Trial Court has overlooked the
  material contradictions in the testimony of <strong>[PW-1]</strong> …</p>

Labels are Title Case - capitalise principal words only, NOT every letter.
===== END CATEGORICAL GROUNDS PATTERN =====

===== TABLE FORMAT (verbatim - borderless HTML) =====
Every data table inside the body uses the same inline-style HTML pattern.
Do NOT use markdown pipe tables (`| col | col |` with `|---|` separators)
- they don't survive the editor edit-save round-trip cleanly.

Pattern (DATA tables - 1px borders so columns are visible):

  <table style="width:100%;border-collapse:collapse;margin:0.5rem 0;">
  <thead>
  <tr>
  <th style="border:1px solid #cbd5e1;padding:8px 12px;text-align:left;">Column 1</th>
  <th style="border:1px solid #cbd5e1;padding:8px 12px;text-align:left;">Column 2</th>
  </tr>
  </thead>
  <tbody>
  <tr>
  <td style="border:1px solid #cbd5e1;padding:8px 12px;vertical-align:top;">Cell value</td>
  <td style="border:1px solid #cbd5e1;padding:8px 12px;vertical-align:top;">Cell value</td>
  </tr>
  </tbody>
  </table>

Use 1px borders for DATA tables (prior applications, case details,
antecedents, co-accused, legal authorities, evidence summaries, statutory
authorities). Each data table MUST be preceded by an introductory numbered
`<p>` paragraph naming what the table contains; never emit a table without
an intro paragraph.

**Omit empty tables.** When the underlying data is absent (no prior
applications, no antecedents, no co-accused), state the absence in prose
inside the numbered paragraph ("No prior application has been filed …")
and omit the table entirely. Never emit a table with placeholder rows.

SIGNATURE blocks and VERIFICATION blocks below are NOT tables - they are
stacked plain `<p>` paragraphs with right-aligned signatory labels and
deliberate top-margin gaps for the actual ink signature.
===== END TABLE FORMAT =====

===== PRAYER BLOCK (template - structure) =====
The PRAYER heading is a centered, bold, underlined `<p>` (not a `##`
heading). The reliefs follow as `(a)`, `(b)`, `(c)` items, each a
separate paragraph with the standard 3.5rem padding. The closing
"interest of justice" relief is mandatory.

  <p style="text-align:center;margin:0.5rem 0;page-break-after:avoid;break-after:avoid;"><strong><u>PRAYER</u></strong></p>

  <p style="padding:0 3.5rem;margin:0.85rem 0 0;">It is, therefore, most humbly and respectfully prayed that this Hon'ble Court may kindly be pleased to:</p>

  <p style="padding:0 3.5rem;margin:0.85rem 0 0;">(a) [substantive principal relief, expressed in <strong>operative</strong> language: <strong>Allow</strong>, <strong>Quash</strong>, <strong>Set aside</strong>, <strong>Grant</strong>, etc., naming the impugned order / FIR / judgment with full particulars];</p>

  <p style="padding:0 3.5rem;margin:0.85rem 0 0;">(b) [ancillary relief - stay, interim direction, costs, conditions];</p>

  <p style="padding:0 3.5rem;margin:0.85rem 0 0;">(c) Pass any other order as this Hon'ble Court may deem fit and proper in the interest of justice.</p>

After the prayer body, emit Place / Date and the post-prayer signature
stack (right-aligned):

  <p style="margin:1.5rem 0 0;padding:0 3.5rem;">Place: <strong>[City]</strong></p>
  <p style="margin:0;padding:0 3.5rem;">Date: <strong>DD/MM/YYYY</strong></p>

  <p style="text-align:right;margin:3.5rem 3.5rem 0;"><strong>[Petitioner / Appellant / Applicant]</strong></p>
  <p style="text-align:right;margin:0 3.5rem;">[Petitioner / Appellant / Applicant Full Name]</p>

  <p style="text-align:right;margin:3.5rem 3.5rem 0;"><strong>Advocate for the [Petitioner / Appellant / Applicant]</strong></p>
  <p style="text-align:right;margin:0 3.5rem;"><strong>[Advocate Name]</strong></p>

Replace `[Petitioner / Appellant / Applicant]` with the SINGLE role label
matching the case type (Petitioner for quashing/SLP, Appellant for
criminal appeal, Applicant for bail/anticipatory bail/revision).
===== END PRAYER BLOCK =====

===== VERIFICATION BLOCK (template - structure) =====
The VERIFICATION heading is a centered, bold, underlined `<p>`. The
deponent paragraph carries the standard 3.5rem padding. The advocate's
"I know the Deponent" certification is LEFT-aligned (not right) - this is
intentional and matches standard Indian-court convention.

  <p style="text-align:center;margin:1.5rem 0 0.5rem;page-break-after:avoid;break-after:avoid;"><strong><u>VERIFICATION</u></strong></p>

  <p style="padding:0 3.5rem;break-inside:avoid;page-break-inside:avoid;">I, <strong>[Deponent Full Name]</strong>, S/O <strong>[Father's Name]</strong>, aged [Deponent Age] years, occupation [Deponent Occupation], the [Petitioner / Appellant / Applicant] in the above matter, residing at [Deponent Address], do hereby state on solemn affirmation that what is stated in the above paragraphs no. [1 to N] is true and correct to the best of my knowledge and information, which I believe to be true. Hence verified at <strong>[City]</strong> on this <strong>DD</strong> day of <strong>[Month, Year]</strong>.</p>

  <p style="margin:1.5rem 0 0;padding:0 3.5rem;">Place: <strong>[City]</strong></p>
  <p style="margin:0;padding:0 3.5rem;">Date: <strong>DD/MM/YYYY</strong></p>

  <p style="text-align:right;margin:3.5rem 3.5rem 0;"><strong>[Petitioner / Appellant / Applicant]</strong></p>
  <p style="text-align:right;margin:0 3.5rem;">[Deponent Full Name]</p>

  <p style="margin:1.5rem 0 0;padding:0 3.5rem;">I know the Deponent.</p>

  <p style="margin:3.5rem 0 0;padding:0 3.5rem;"><strong>Advocate for the [Petitioner / Appellant / Applicant]</strong></p>
  <p style="margin:0;padding:0 3.5rem;"><strong>[Advocate Name]</strong></p>
===== END VERIFICATION BLOCK =====

===== SIGNATURE BLOCK - CRITICAL LAYOUT RULE =====
Signature blocks are STACKED `<p>` paragraphs. Emit them VERBATIM. Do NOT
wrap them in a `<table>` (visible default borders), do NOT use 3-column /
flex / column container layouts (wrapping mess on long names).

POST-PRAYER block (entire stack right-aligned):
  Place left -> Date left -> [vertical signature gap via margin] ->
  Role BOLD right -> party's typed name plain right ->
  [gap] -> "Advocate for the [Role]" BOLD right ->
  advocate's typed name BOLD right.

POST-VERIFICATION block (deponent right, advocate-cert LEFT):
  Place left -> Date left -> [signature gap] ->
  Role BOLD right + typed name plain right (deponent column) ->
  "I know the Deponent." LEFT-aligned with body padding ->
  [signature gap] -> "Advocate for the [Role]" BOLD LEFT ->
  advocate's typed name BOLD LEFT.

The advocate certification under VERIFICATION is left-aligned (NOT
right). That is intentional and different from the post-PRAYER block - in
standard Indian-court convention the advocate's "I know the deponent"
certification sits at the bottom-left of the page.

Both typed names (party + advocate) MUST appear - pull from STRUCTURED
INPUT, leave `[Advocate Name]` if not provided.
===== END SIGNATURE BLOCK RULE =====

===== STATUTE REFERENCE DISCIPLINE =====
Every statutory section cited in the body MUST be expressed in its dual
form - the old provision (CrPC / IPC / Evidence Act, 1872) AND the new
provision (BNSS, 2023 / BNS, 2023 / BSA, 2023). Examples:

  - Section 438 CrPC / Section 482 BNSS (anticipatory bail)
  - Section 439 CrPC / Section 483 BNSS (regular bail)
  - Section 167(2) CrPC / Section 187 BNSS (default bail)
  - Section 482 CrPC / Section 528 BNSS (inherent powers)
  - Section 374 CrPC / Section 415 BNSS (criminal appeal)
  - Section 397/401 CrPC / Section 438 BNSS (revision)
  - Section 389 CrPC / Section 434 BNSS (suspension of sentence)
  - Section 420 IPC / Section 318 BNS (cheating)
  - Section 506 IPC / Section 351 BNS (criminal intimidation)
  - Section 376 IPC / Section 64 BNS (rape)
  - Section 302 IPC / Section 103 BNS (murder)
  - Section 304B IPC / Section 80 BNS (dowry death)
  - Section 498A IPC / Section 85-86 BNS (cruelty by husband / relatives)

When the source matter (FIR / chargesheet / impugned order) was registered
under the OLD code, cite both old and new on first mention; thereafter
either form is acceptable. Do not invent statute numbers.
===== END STATUTE REFERENCE DISCIPLINE =====
"""
