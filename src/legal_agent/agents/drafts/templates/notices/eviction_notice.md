<!--
STRUCTURAL REFERENCE for an Eviction Notice under Section 106 of the Transfer of
Property Act, 1882, and applicable State Rent Control Act.

How to use this reference:
1. Substitute every `[Bracketed Field]` with values from STRUCTURED INPUT and
   REFERENCE DOCUMENTS CONTEXT.
2. A bracket survives in your final output ONLY when the value is absent from
   BOTH sources. Use clear, advocate-editable labels like `[Tenancy Commencement Date]`,
   `[Monthly Rent]`, `[Suit Premises Description]`. Never emit `[XX]`, `_____`,
   `XXXX`, `[NOT PROVIDED]`.
3. Do not invent values. Do not silently drop a line because data is missing —
   keep the line and bracket the missing field.
4. The stationery + RPAD banner + Dated line + recipient/sender/subject blocks
   are defined in the NOTICE BASELINE embedded in the system prompt. Start
   directly with the numbered body paragraphs below.
5. Use `<strong>` (NOT markdown `**`) for emphasis inside `<p>` and `<td>` tags.
-->

===== BODY PARAGRAPH SEQUENCE (eviction notice) =====

After the stationery / RPAD banner / dated line / recipient / sender /
SUBJECT / opener (per BASELINE), emit numbered `<p>` paragraphs in this
order. Each `<p>` carries `style="padding:0 3.5rem;"`.

The SUBJECT line - one line, 25 words max. Pattern:

  <p style="padding:0 3.5rem;margin:1.25rem 0 0;"><strong>SUBJECT: Notice u/s 106 TP Act 1882 - Termination of tenancy and demand for vacant possession of [short premises ref, e.g. "Flat 426, Lotus Nandanvan, Moshi"].</strong></p>

The full property description, arrears computation, mesne profits, and
consequences belong in the body, not in the subject.

**Paragraph 1 - Client identity and ownership / landlord status.**
Full name (in `<strong>`), age, occupation, address. State the
landlord's title to the suit premises with documentary support: "the
absolute owner / lawful landlord / lessor of the premises hereinafter
described, by virtue of registered Sale Deed dated DD/MM/YYYY bearing
Document No. ... of ... registered at the Office of the Sub-Registrar,
[Office]" (or by virtue of will / partition deed / gift deed /
inheritance / power of attorney - whichever applies). One paragraph.

**Paragraph 2 - Description of the suit premises.**
Precise description sufficient for execution of a decree:
- Building name / society / colony
- Flat / unit / shop / godown number
- Floor and carpet area (sq. ft.)
- Survey No. / CTS No. / Property Tax No. (Mun. Corp.)
- Full address with city, district, state, pincode
- Boundaries (north, south, east, west) where the property is a
  standalone plot

If a registered lease deed exists, reference its document number and
date.

**Paragraph 3 - Tenancy particulars.**
The mode of creation of the tenancy:
- Registered Lease Deed dated DD/MM/YYYY for [period] / Leave and
  Licence Agreement dated DD/MM/YYYY for [period] / oral monthly
  tenancy commencing DD/MM/YYYY;
- Monthly rent of <strong>Rs. [Rent]/- (Rupees [Rent in Words]
  Only)</strong> payable in advance on or before the [Nth] day of
  every English calendar month;
- Security deposit of <strong>Rs. [Deposit]/-</strong> received from
  you on DD/MM/YYYY, refund of which is subject to adjustment of
  arrears, damages, and lawful deductions;
- Tenancy month: from the [1st] of every month to the last day
  thereof (this defines the validity of the §106 TP Act notice
  period - the notice MUST expire on the last day of a tenancy
  month).

**Paragraph 4 - Rent default / breach particulars.**
This paragraph contains a borderless 2-column HTML particulars table
where there are 2 or more months in arrears. Pattern:

  <p style="padding:0 3.5rem;">4. You have committed wilful and
  continuing default in payment of rent. The arrears as on DD/MM/YYYY
  are as follows:</p>

  <table style="width:100%;border-collapse:collapse;margin:0.5rem auto;
  max-width:640px;">
  <thead>
  <tr>
  <th style="border:1px solid #cbd5e1;padding:8px 12px;text-align:left;">Period</th>
  <th style="border:1px solid #cbd5e1;padding:8px 12px;text-align:right;">Rent (Rs.)</th>
  </tr>
  </thead>
  <tbody>
  <tr><td style="border:1px solid #cbd5e1;padding:8px 12px;">DD/MM/YYYY to DD/MM/YYYY ([N] months)</td><td style="border:1px solid #cbd5e1;padding:8px 12px;text-align:right;">[arrears in figures]</td></tr>
  <tr><td style="border:1px solid #cbd5e1;padding:8px 12px;">Interest @ [rate]% p.a.</td><td style="border:1px solid #cbd5e1;padding:8px 12px;text-align:right;">[interest]</td></tr>
  <tr><td style="border:1px solid #cbd5e1;padding:8px 12px;"><strong>Total arrears</strong></td><td style="border:1px solid #cbd5e1;padding:8px 12px;text-align:right;"><strong>[total]</strong></td></tr>
  </tbody>
  </table>

  <p style="padding:0 3.5rem;">that is to say, total arrears of
  <strong>Rs. [total]/- (Rupees [total in words] Only)</strong> as on
  DD/MM/YYYY, together with rent accruing from DD/MM/YYYY at the
  contractual rate until the date of delivery of vacant possession.</p>

If the ground for eviction is NOT default of rent (i.e., bona-fide
requirement / unlawful sub-letting / change of user / nuisance /
structural alteration), substitute Paragraph 4 with a factual recital
of the breach with specific dates - and SKIP the arrears table.

**Paragraph 5 - Other grounds (where applicable).**
Where multiple grounds are pressed (e.g., default + unlawful
sub-letting), each ground gets its own numbered paragraph with
specific dates and corroborating facts. Cite the relevant clause of
the lease deed and/or the relevant section of the State Rent Control
Act:

  - "Your unauthorised sub-letting of a portion of the suit premises
    to <strong>[Sub-tenant Name]</strong> on or about DD/MM/YYYY,
    without the written consent of my client, is a breach of
    <strong>Clause [N]</strong> of the registered Lease Deed dated
    DD/MM/YYYY, and constitutes a ground for eviction under
    <strong>Section [...]</strong> of the <strong>[State] Rent
    Control Act, [Year]</strong>."

**Paragraph 6 - Prior demands / opportunities to cure.**
Dates of prior demands, reminders, meetings, and the addressee's
response or silence. State that the notice period afforded by Section
106 TP Act is itself an opportunity to cure default and deliver
peaceful possession.

**Paragraph 7 - Statutory characterisation.**
Inline statement:

  "By reason of your aforesaid wilful default and breach, the tenancy
  in respect of the suit premises stands forfeited and / or is liable
  to be terminated. Without prejudice to my client's rights, my
  client hereby terminates the said tenancy under <strong>Section
  106 of the Transfer of Property Act, 1882</strong> [where
  applicable, add: 'read with Section [...] of the
  <strong>[State] Rent Control Act, [Year]</strong>']. Your continued
  occupation of the suit premises after the expiry of the notice
  period set out below shall constitute trespass and entitle my
  client to recover mesne profits / damages for use and occupation
  at the prevailing market rent."

**Paragraph 8 - DEMAND (TAKE NOTICE).**
Inline `<strong>TAKE NOTICE</strong>` opener. The demand calculates
the §106 TP Act notice period: monthly tenancy = 15 days expiring on
the last day of a tenancy month; yearly tenancy = 6 months expiring
with the end of the tenancy year. Use the tenancy month from
Paragraph 3 to compute the SPECIFIC EXPIRY DATE.

  Required acts:

  (a) Deliver vacant and peaceful possession of the suit premises
  described in Paragraph 2 above to my client on or before
  <strong>DD/MM/YYYY</strong> [the last day of the tenancy month
  falling 15 days after receipt; for yearly tenancy adjust to 6
  months ending with the year];

  (b) Pay arrears of rent in the sum of
  <strong>Rs. [total arrears]/- (Rupees [total in words]
  Only)</strong>, with further rent accruing at
  <strong>Rs. [Rent]/-</strong> per month until the date of
  delivery of vacant possession;

  (c) Pay mesne profits / damages for use and occupation from the
  date of expiry of the notice period until the actual date of
  delivery of vacant possession at the prevailing market rate (which
  my client estimates at <strong>Rs. [Market Rent]/-</strong> per
  month, without prejudice);

  (d) Pay notice costs of <strong>Rs. [notice costs]/-</strong>;

  (e) Settle all outstanding municipal taxes / electricity / water /
  maintenance charges in respect of the suit premises up to the date
  of delivery of vacant possession.

**Paragraph 9 - CONSEQUENCES (TAKE FURTHER NOTICE).**
Inline `<strong>TAKE FURTHER NOTICE</strong>` opener:

  Should you fail to comply with the demands aforesaid, my client
  shall be constrained to institute -

  (i) A <strong>Suit for Eviction / Possession</strong> against you
  before the [Court / Rent Control Court / Small Causes Court at
  City], under <strong>Section 106 of the Transfer of Property Act,
  1882</strong> [where applicable, add: 'read with Section [...] of
  the <strong>[State] Rent Control Act, [Year]</strong>'];

  (ii) A claim for <strong>arrears of rent</strong> together with
  interest;

  (iii) A claim for <strong>mesne profits / damages</strong> for use
  and occupation from the date of expiry of the notice period until
  delivery of vacant possession at the prevailing market rate;

  (iv) A claim for <strong>costs of the suit</strong>, advocate's
  fees, and incidental expenses;

  all entirely at your risk, responsibility, cost, and consequence.

  Where applicable, my client expressly reserves the right to seek
  interim relief by way of injunction restraining you from creating
  third-party rights, parting with possession, or making any
  alteration to the suit premises pending the suit.

**Paragraph 10 - Reservation of rights.**
"This notice is issued without prejudice to any other rights,
remedies, and contentions of my client - civil, criminal, or
otherwise - all of which are expressly reserved. Acceptance of any
amount towards arrears shall not be construed as waiver of the
termination effected hereby."

**Paragraph 11 - Governing law and jurisdiction.**
Court / forum at <strong>[City]</strong> with exclusive jurisdiction.
For tenancies covered by State Rent Control Acts, the Court of Small
Causes / Rent Controller / Civil Judge (as designated by the State
Act) is the proper forum.

After the body, emit the SIGNATURE BLOCK and Copy-to / Enclosures /
Mode of Service blocks per the BASELINE.

The Enclosures list MUST mention:
  1. Photocopy of the registered Lease Deed / Leave and Licence
     Agreement dated DD/MM/YYYY (if any).
  2. Photocopy of the most recent rent receipt / bank statement
     evidencing default.
  3. Photocopy of the document of title (Sale Deed / Will / Gift
     Deed) evidencing my client's ownership.
===== END BODY PARAGRAPH SEQUENCE =====
