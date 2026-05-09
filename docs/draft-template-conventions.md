# Draft Template Conventions — Indian-Court Drafting Style Guide

**Purpose.** Describe the writing, layout, indentation, and spacing conventions we settled on for the **interim application** drafting agent (`court_filing_agent.py::INTERIM_APPLICATION_SYSTEM_PROMPT`) so the same conventions can be replicated for other document types — notice, bail application, anticipatory bail, criminal appeal, SLP, plaint, etc. Each document type has its **own substantive content and section structure**, but the *style baseline* (writing voice, paragraph layout, signature blocks, headings, spacing) should be identical to keep all drafts looking like they came from the same advocate's chambers.

This file is the source of truth. When migrating an existing agent or adding a new one, treat each section here as a checklist.

---

## 1. Architecture — what the agent emits vs. what is rendered separately

The drafting pipeline composes the final markdown in two layers:

```
┌──────────────────────────────────────────────────┐
│  Cause-title HTML  (rendered DETERMINISTICALLY    │
│   by `agents/drafts/cause_title.py`,              │
│   prepended to the agent's output)                │
├──────────────────────────────────────────────────┤
│  Body markdown     (emitted by the LLM agent      │
│   following the agent's SYSTEM_PROMPT)            │
└──────────────────────────────────────────────────┘
```

**The agent should NEVER emit cause-title elements.** No `# IN THE HON'BLE …`, no `**Plaintiff**` party block, no `Vs.` line, no document-title heading. These are rendered by `cause_title.py::render_cause_title_html` and prepended via `prepend_cause_title_to_draft`. The agent starts directly at the body.

To opt a new agent into this rendering, override:

```python
class MyAgent(BaseDraftingAgent):
    def _renders_cause_title(self, deps: DraftingDependencies) -> bool:
        return True
```

Today this is wired for `CourtFilingAgent` when `_is_interim_application(deps)` is true. To add `NoticeAgent`, `BailAgent`, etc., override `_renders_cause_title` similarly. Court-filing-style drafts (anything that opens with `IN THE HON'BLE …`) should opt in. Notices and contracts that have a different opening (sender block, recitals) should NOT.

---

## 2. Cause-title rendering (when applicable)

**File:** `src/legal_agent/agents/drafts/cause_title.py`

The cause title is rendered as inline-styled HTML and is editor-agnostic. Key points to know:

- **Document title is derived from CONTENT, not from the user's filename.** The LLM extractor (rule 10 in `_EXTRACT_SYSTEM_PROMPT`) must produce something like `"APPLICATION FOR TEMPORARY INJUNCTION UNDER ORDER 39 RULES 1 AND 2 CPC"` or `"NOTICE UNDER SECTION 138 OF THE NEGOTIABLE INSTRUMENTS ACT, 1881"`. The user-supplied filename (`"Interim Application Test 12"`) is informational only. If the LLM returns null, the renderer emits `[Document Title]` for the advocate to fill — we deliberately do NOT fall back to the filename.
- **Every `<p>` in the cause-title block carries inline `style="margin:0.25rem 0;line-height:1.375;"`** to pin the tight density even when the editor's default paragraph spacing is loosened. Constant `_P_STYLE` in `cause_title.py`.
- **Mob+role line** uses a borderless table (`<table class="cause-title-row">`) so `Mob.no. NNNN` and `………Plaintiff` sit on the same line, role flush right.
- **Role tags inherit from the parent suit** — `Plaintiff/Defendant` for civil, `Petitioner/Respondent` for writ, `Applicant/Respondent` only when the source is silent. The extractor system-prompt already encodes this; new agents don't need to reimplement.
- **Multiple parties**: `ordinal=1, 2, 3, …`; the renderer emits `**Defendant No. 1**` blocks above the name when there are multiple parties on a side.
- **No `---` horizontal rules inside the cause-title block.** Court banner → AT location → case number → first side → Vs. → second side → document title flow as one cohesive block.

**Sentinel comments** (`<!-- cause-title:start -->` / `<!-- cause-title:end -->`) wrap the rendered block so the prepend is idempotent and post-processing can locate it.

---

## 3. Body structure — flat numbered HTML `<p>` blocks, no section headings

**The two single most important rules:**

1. **Drop the `##` section headings.** Indian-court drafts read as continuous numbered prose. We do NOT emit `## BRIEF FACTS`, `## PRIMA FACIE CASE`, `## IRREPARABLE HARM`, etc. These were inflating drafts beyond convention.

2. **Emit numbered paragraphs as plain HTML `<p>` blocks, NOT as a markdown numbered list.** When the agent emits `1. text\n\n2. text\n\n3. text` (markdown numbered-list syntax), `marked` parses it into `<ol><li>...</li><li>...</li></ol>`. That structure does not survive the contentEditable / TipTap edit-save round-trip — the editor collapses everything into one paragraph with literal `**` markers and explicit "1.", "2." text. Plain HTML `<p>` blocks pass through marked unchanged and round-trip cleanly.

**Pattern (HTML):**

```html
<p>The applicant respectfully submits as follows:</p>

<p>1. The present [document type] is filed in [Case Type] No. [Number] / [Year]
pending before this Hon'ble Court.</p>

<p>2. The applicant herein, <strong>[Full Name]</strong>, age [Age] years,
occupation [Occupation], residing at [Address], is the
<strong>[First Party Role]</strong> in the aforesaid suit, and the said suit
concerns [one-line description of parent suit].</p>

<p>3. The respondent, <strong>[Full Name]</strong>, age [Age] years, …</p>

<p>4. [Substantive facts — underlying transaction, lease, agreement, FIR, etc.
with specific dates DD/MM/YYYY and amounts in figures + words.]</p>

<p>5. [Prima facie case / legal basis paragraph — names the document, date,
parties; cites the applicable statutory provision.]</p>

<p>6. [Irreparable harm paragraph — concrete harm; why damages aren't adequate.]</p>

<p>7. [Balance of convenience / urgency paragraph.]</p>

<p>8. [Closing paragraph — prima facie case + balance of convenience +
irreparable loss summary.]</p>
```

**Inside `<p>` blocks, use HTML emphasis tags, NOT markdown:**

- `<strong>Name</strong>` — NOT `**Name**` (markdown emphasis is not parsed inside HTML blocks; the asterisks would render as literal text)
- `<em>italic</em>` — NOT `*italic*`

**Variations per document type:**
- **Bail application:** opening line + numbered paragraphs covering FIR particulars, sections invoked, applicant's role, custody status, grounds for bail, conditions willing to abide by.
- **Notice:** opening salutation (`Sir/Madam,`) + numbered paragraphs covering the underlying transaction, the breach/default, the demand, the consequence of non-compliance, the deadline.
- **Plaint / civil suit:** numbered paragraphs covering jurisdiction (territorial / pecuniary / subject-matter), facts, cause of action (with date), limitation, valuation, grounds.
- **Writ petition:** numbered paragraphs covering jurisdiction (Article 226/32), facts, fundamental/statutory rights violated, no alternative remedy, grounds.

In every case: **flat numbered paragraphs (1., 2., 3., …)**. No sub-numbering inside the body (no `1.1`, `1.2`, `2.1`). A single flat list. Blank line between paragraphs.

**No `---` horizontal rules anywhere in the body.** Section breaks are signalled only by the centered + bold + underlined PRAYER and VERIFICATION headings.

---

## 4. PRAYER and VERIFICATION — centered + bold + underlined HTML

Both are NOT `## ` markdown headings. They are emitted as inline-styled HTML paragraphs matching the cause-title court-banner style:

```html
<p style="text-align:center;margin:0.5rem 0;"><strong><u>PRAYER</u></strong></p>
<p style="text-align:center;margin:0.5rem 0;"><strong><u>VERIFICATION</u></strong></p>
```

This is the same convention as the centered+bold+underlined Stay Application document title and the `IN THE HON'BLE…` court banner — every centered heading in the document uses the same shape.

### PRAYER — body

Standard lead-in:

> It is, therefore, most humbly and respectfully prayed that this Hon'ble Court may kindly be pleased to:

Reliefs as `(a)`, `(b)`, `(c)`, `(d)`, `(e)`. Each relief precise enough that a judge can grant it as written. Final clauses always:

- An "ad-interim relief in terms of prayer (a) above ex-parte" clause when seeking interim relief.
- "Award costs of this [filing] to the [First Party Role]"
- "Pass any other order as this Hon'ble Court may deem fit and proper in the interest of justice."

### VERIFICATION — body

Standard phrasing (mirroring the user-supplied reference):

> I, **[First Party Full Name]**, aged [First Party Age] years, occupation [First Party Occupation], the [First Party Role] in the above matter, residing at [First Party Address], do hereby state on solemn affirmation that what is stated in the above paragraphs no. [1 to N] is true and correct to the best of my knowledge and information, which I believe to be true. Hence verified at **[City]** on this **[DD]** day of **[Month, Year]**.

`[1 to N]` — substitute the actual paragraph range. If unknown, leave the bracket for the advocate.

---

## 5. Signature blocks — borderless HTML tables

Two layouts. Use the right one for the right slot. Both are emitted **verbatim** — do not rewrite as plain paragraphs.

### Post-PRAYER — 3-column borderless table

| Place: [City]      |                          |                                       |
| ------------------ | ------------------------ | ------------------------------------- |
| Date: DD/MM/YYYY   | **[First Party Role]**   | **Advocate for the [First Party Role]** |
|                    |                          | **[Advocate Name]**                   |

```html
<table style="width:100%;border-collapse:collapse;border:0;margin:0.5rem 0;">
<tbody>
<tr>
<td style="border:0;padding:0;text-align:left;vertical-align:top;width:40%;">Place: [City]</td>
<td style="border:0;padding:0;text-align:center;vertical-align:top;width:20%;"></td>
<td style="border:0;padding:0;text-align:right;vertical-align:top;width:40%;"></td>
</tr>
<tr>
<td style="border:0;padding:0;text-align:left;vertical-align:top;">Date: DD/MM/YYYY</td>
<td style="border:0;padding:0;text-align:center;vertical-align:top;"><strong>[First Party Role]</strong></td>
<td style="border:0;padding:0;text-align:right;vertical-align:top;"><strong>Advocate for the [First Party Role]</strong></td>
</tr>
<tr>
<td style="border:0;padding:0;"></td>
<td style="border:0;padding:0;"></td>
<td style="border:0;padding:0;text-align:right;vertical-align:top;"><strong>[Advocate Name]</strong></td>
</tr>
</tbody>
</table>
```

### Post-VERIFICATION — 2-column borderless table + below

| Place: [City]    |                        |
| ---------------- | ---------------------- |
| Date: DD/MM/YYYY | **[First Party Role]** |

```html
<table style="width:100%;border-collapse:collapse;border:0;margin:0.5rem 0;">
<tbody>
<tr>
<td style="border:0;padding:0;text-align:left;vertical-align:top;width:50%;">Place: [City]</td>
<td style="border:0;padding:0;text-align:right;vertical-align:top;width:50%;"></td>
</tr>
<tr>
<td style="border:0;padding:0;text-align:left;vertical-align:top;">Date: DD/MM/YYYY</td>
<td style="border:0;padding:0;text-align:right;vertical-align:top;"><strong>[First Party Role]</strong></td>
</tr>
</tbody>
</table>

I know the Deponent.

**Advocate for the [First Party Role]**
```

**Why borderless tables instead of flex/columns?** Markdown editors (TipTap + the legacy contenteditable) handle tables reliably and identically. Inline `border:0;padding:0;` overrides the editor's default `[&_table]:border [&_td]:border` Tailwind rules. CSS columns / flexbox don't survive markdown→HTML round-trips.

---

## 6. Substitution & placeholder discipline

### `[Bracketed Field]` is a substitution slot, not output

Every `[Bracketed Field]` in a template is a slot to fill with values from `STRUCTURED INPUT` (wizard form) and `REFERENCE DOCUMENTS CONTEXT` (uploaded source PDF text).

A bracket survives in the final output **only when the value is absent from BOTH sources** — and even then, the bracket label must be advocate-editable, like `[Applicant Mobile]`, `[Court Name]`, `[Statutory Provision]`.

**Never emit:** `[XX]`, `_____`, `XXXX`, `[NOT PROVIDED]`, `[Amount]`, `[Name]`, `[DD]`, `[Month, Year]`, `[Title — Shri/Smt/Kumari/Mr./Ms.]`. These are anonymous placeholders that signal a drafting shortcut, not a real gap.

### Precedence of sources

When STRUCTURED INPUT and REFERENCE DOCUMENTS disagree:
1. Prefer STRUCTURED INPUT (advocate explicitly typed it for THIS draft).
2. If STRUCTURED INPUT is silent, take from REFERENCE DOCUMENT.
3. If BOTH are silent, leave a clearly-named bracket. Never fabricate.

### Honorifics

Pick from source: `Shri`, `Smt.`, `Kumari`, `Mr.`, `Ms.`, `M/s`. Emit inline before the name. Do NOT emit literal `[Title — Shri/Smt/…]`.

### Aadhaar masking

Mask to last four: `XXXX-XXXX-1234`. Never emit a full Aadhaar.

---

## 7. Indian-legal formatting rules (every draft)

### Amounts

Always in figures **and** words with Indian numbering:

> Rs. 4,25,000/- (Rupees Four Lakh Twenty-Five Thousand Only)

Never the international comma style (`1,250,000`).

### Dates

- DD/MM/YYYY for specific dates.
- "on or about [Month] [Year]" for approximate.
- Today's date is supplied in the user prompt under `Today's date:`. Use as the execution date when the user is silent; default the commencement / start date to the execution date.

### Statutes

Reference Indian statutes by name and year, with section where relevant:

- Indian Contract Act, 1872
- Code of Civil Procedure, 1908 (CPC)
- Code of Criminal Procedure, 1973 (CrPC) / Bharatiya Nagarik Suraksha Sanhita, 2023 (BNSS)
- Indian Penal Code, 1860 (IPC) / Bharatiya Nyaya Sanhita, 2023 (BNS)
- Negotiable Instruments Act, 1881
- Arbitration and Conciliation Act, 1996
- Digital Personal Data Protection Act, 2023 (DPDP)
- Maternity Benefit Act, 1961 (as amended 2017)
- Sexual Harassment of Women at Workplace Act, 2013 (PoSH)

For employment / commercial **data protection** clauses use **DPDP Act, 2023** — not IT Act §43A or SPDI Rules.

### Defined terms

Choose ONE per party and use it consistently throughout:
- `**"Employer"**` not "Company" later
- `**"Employee"**` not "Executive" later

Drift between defined terms is a drafting defect.

### Case-law citations

```
**[Case Name]** — [Citation]
```

Examples:
- `**Sushila Aggarwal v. State (NCT Delhi)** — (2020) 5 SCC 1`
- `**Arnesh Kumar v. State of Bihar** — (2014) 8 SCC 273`
- `**Sharad Birdhichand Sarda v. State of Maharashtra** — AIR 1984 SC 1622`

Format rules:
- Case name **bold**, em-dash (—), citation in plain text.
- SC: `(YYYY) Vol SCC Page`. AIR: `AIR YYYY Court Page`. HC: `YYYY (Vol) Abbreviation Page`.
- No parentheses around the citation. No markdown links.

Only cite cases returned by `legal_case_search`. If no case found, write the ground without a citation rather than inventing one.

---

## 8. Editor CSS contract

The body markdown is rendered in two editors that must look identical:

- `/drafting` — TipTap editor (`packages/web/src/components/editor/document-editor.tsx`)
- `/documents` and case-workspace draft preview — custom `contentEditable` styled by `.legal-document` rules in `packages/web/src/index.css`

The `.legal-document` rules and the TipTap class list are kept in sync. Baseline:

| Property | Value |
| --- | --- |
| Font | Times New Roman, 12pt |
| Container | `max-width: 820px; padding: 32px 40px;` |
| Line-height | `1.5` |
| `<p>` margin | `8px 0` |
| `<h1>` / `<h2>` margins | `16px 0 8px` |
| `<h3>` margin | `12px 0 8px` |
| Table border | `1px solid #cbd5e1` (ledger-gray-300) |
| Table cell padding | `8px 12px` |
| Cause-title `<p>` (inline override) | `margin:0.25rem 0;line-height:1.375;` |
| Cause-title mob+role table (inline + class) | `border:0;padding:0;` via `.cause-title-row` |

**When you change one, change the other.** The `.legal-document` block in `index.css` carries a comment reminding the next contributor of this. The cause-title borderless table override is mirrored in both stylesheets.

---

## 9. PDF export

Service: `legal-agent-service` via WeasyPrint (`agents/translation/pdf_builder.py`).
Stylesheets: `agents/translation/styles/{default,letter,contract,court_filing}.css`.

**No page numbers.** All four stylesheets had `@page { @bottom-center { content: counter(page); } }` — removed. The `@page` size/margin block is preserved.

**No grey horizontal rules in PDFs.** Same rationale as on-screen — the document should read as one uniform block.

---

## 10. Per-agent customization — what changes between document types

When you build `NoticeAgent`, `BailAgent`, etc., copy the structural baseline (sections 1–9 above) and customize **only the substantive content**:

| What stays identical | What varies per document type |
| --- | --- |
| Cause-title rendering layer | Document title (LLM-derived from content) |
| Flat numbered body paragraphs (1., 2., 3., …) | Section ordering, content focus, statutory hooks |
| No `##` section headings in the body | Opening line ("The applicant respectfully submits…", "Most Respectfully Showeth:", "Sir/Madam,", etc.) |
| Centered + bold + underlined PRAYER | Prayer reliefs vary per document type |
| Centered + bold + underlined VERIFICATION | (mostly identical — paragraph range differs) |
| Signature blocks (3-col post-prayer, 2-col post-verification) | Role label (Plaintiff / Petitioner / Applicant / Complainant / Accused / Sender) |
| Editor CSS baseline | (no variation — one font, one spacing) |
| PDF stylesheet | Use the right one: `court_filing.css` for filings, `letter.css` for notices, `contract.css` for agreements, `default.css` otherwise |
| Substitution & placeholder discipline | (no variation) |
| Indian-legal formatting (amounts, dates, statutes) | (no variation) |

### Document-type-specific opening lines

- **Plaint / Civil Suit:** `The plaintiff states as under:`
- **Writ Petition:** `The petitioner most respectfully submits as under:`
- **Interim Application:** `The applicant respectfully submits as follows:`
- **Bail Application:** `The applicant most respectfully submits as under:`
- **Anticipatory Bail:** `The applicant most respectfully submits as under:`
- **Notice (Sec 138 / demand / legal):** `Sir / Madam,` (sender block above; recipient in salutation)
- **Affidavit:** `I, [Name], do hereby solemnly affirm and state as under:`
- **Written Statement:** `The defendant submits as under:`
- **Application (general):** `The applicant most respectfully showeth:`

### Document-type-specific role labels

The cause-title `Party.role` field is restricted to `Plaintiff | Defendant | Petitioner | Respondent | Applicant`. For document types where the canonical label is different (`Complainant`, `Accused`, `Appellant`, `Sender`, `Addressee`, `Deponent`):

- For **bail / criminal appeals**: use `Applicant` for the accused; the verification + signature blocks should still say `Applicant` not `Accused`.
- For **notices**: cause title doesn't apply (no court). Render a sender block + recipient block manually in the agent's body (not via `cause_title.py`).
- For **affidavits**: `Applicant` cause-title role; "Deponent" appears only at the bottom signature block, NOT in the cause title.

If a new role label is genuinely required, extend `cause_title.py::RoleLabel` Literal — but prefer reusing one of the five canonical roles.

---

## 11. Checklist for adding a new document type

When creating `MyNewAgent`:

1. **Decide if cause-title rendering applies.**
   - Court-filing-style (opens with "IN THE HON'BLE …") → opt in via `_renders_cause_title` returning True.
   - Letter-style (notice, demand notice) → do NOT opt in. Render sender / recipient block in the agent's body.

2. **Write the system prompt** — copy `INTERIM_APPLICATION_SYSTEM_PROMPT` as the starting structure. Replace:
   - `SPECIALIZED FOCUS` block — describe the document type and its sub-types.
   - `===== BODY STRUCTURE =====` instructions — specify what goes in paragraph 1, 2, 3, … for THIS document type.
   - PRAYER reliefs — list the relief types relevant to the document type.
   - Verification phrasing — usually identical, but adjust if the document type doesn't take an affidavit (e.g., notices skip verification).

3. **DO NOT include** in the prompt:
   - `## ` section headings inside the body.
   - `---` horizontal rules anywhere.
   - Cause-title HTML (if `_renders_cause_title` is True).

4. **DO include** in the prompt (verbatim, in the right slots):
   - Centered + bold + underlined PRAYER and VERIFICATION HTML paragraphs.
   - 3-column borderless signature table after PRAYER.
   - 2-column borderless signature table after VERIFICATION + "I know the Deponent." + counsel sign-off.
   - Substitution-contract block at the top.
   - Formatting notes (Indian formatting, statute references, citation format) — copy from interim app prompt.

5. **Register the agent**:
   - Add to `DocumentType` enum in `models/documents.py`.
   - Map in `services/draft_service.py::DraftService._agent_classes` and `_agents`.

6. **Add few-shot examples** in `data/examples.json` keyed by `(document_type, subtype, language)` — load via `examples_loader.py`.

7. **Pick the PDF stylesheet** in `agents/translation/css_resolver.py` mapping for the new `DocProfile`.

8. **Test** on at least 3 sub-types using `test_docs/compare_models.py` and inspect output in `/drafting`, `/documents`, and the case-workspace draft preview. All three should look identical (sections 1, 8 above).

---

## 12. Reference files

| File | Role |
| --- | --- |
| `src/legal_agent/agents/drafts/cause_title.py` | Deterministic cause-title HTML rendering + LLM extractor for cause-title data |
| `src/legal_agent/agents/drafts/court_filing_agent.py` | `INTERIM_APPLICATION_SYSTEM_PROMPT` — the gold-standard body prompt to mirror |
| `src/legal_agent/agents/drafts/base.py` | `BaseDraftingAgent`, `DraftingDependencies`, `BASE_SYSTEM_PROMPT` (drafting principles + format rules) |
| `src/legal_agent/services/draft_service.py` | Orchestration: routes document type → agent, builds `DraftingDependencies`, calls `agent.draft()` |
| `src/legal_agent/agents/translation/styles/court_filing.css` | WeasyPrint stylesheet for court filings (PDF export) |
| `knowlex-platform-ui/packages/web/src/index.css` | `.legal-document` rules — kept in sync with TipTap |
| `knowlex-platform-ui/packages/web/src/components/editor/document-editor.tsx` | TipTap editor — source of truth for editor CSS baseline |

---

## 13. Drift to avoid (lessons from this round)

- **Don't put the user's filename in the document title.** The LLM must derive a real legal title from content. Filename is informational only. Renderer falls back to `[Document Title]` placeholder, never to the filename.
- **Don't sprinkle `##` headings through the body.** The LLM is tempted because it makes drafts feel "structured", but Indian-court drafts are flat numbered prose. Pure visual headings break the convention.
- **Don't emit body paragraphs as markdown numbered lists.** `1. text\n\n2. text\n\n3. text` becomes `<ol><li>` after `marked`, and the `<ol><li>` structure collapses on contentEditable / TipTap edit-save round-trips into a wall of text with literal `**` markers. Always emit body paragraphs as `<p>N. text</p>` HTML blocks with `<strong>` for emphasis instead of `**bold**`.
- **Don't use `---` horizontal rules anywhere.** Section breaks come from the centered headings (PRAYER / VERIFICATION) alone.
- **Don't rewrite signature blocks as plain paragraphs.** They must be borderless HTML tables — emit verbatim from the template. The 3-col / 2-col layout is intentional.
- **Don't change `.legal-document` CSS without updating the TipTap class list (and vice versa).** They render the same content; drift will show up as visual inconsistency between `/drafting` and `/documents`.
- **Don't add page numbers to PDFs.** WeasyPrint stylesheets are stripped of `@bottom-center { content: counter(page); }` deliberately.

---

*Last updated: 2026-05-09 — established during the interim application rollout.*
