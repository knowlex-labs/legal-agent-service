<!--
STRUCTURAL REFERENCE for a Demand Notice (Recovery of Money) drafted on advocate's
letterhead under Section 73 / Section 74 of the Indian Contract Act, 1872.

How to use this reference:
1. Substitute every `[Bracketed Field]` with values from STRUCTURED INPUT and
   REFERENCE DOCUMENTS CONTEXT.
2. A bracket survives in your final output ONLY when the value is absent from
   BOTH sources. Use clear, advocate-editable labels like `[Principal Amount]`,
   `[Invoice Number]`, `[Due Date]`. Never emit `[XX]`, `_____`, `XXXX`,
   `[NOT PROVIDED]`.
3. Do not invent values. Do not silently drop a line because data is missing —
   keep the line and bracket the missing field.
4. The stationery + RPAD banner + Dated line + recipient/sender/subject blocks
   are defined in the NOTICE BASELINE embedded in the system prompt. Start
   directly with the numbered body paragraphs below.
5. Use `<strong>` (NOT markdown `**`) for emphasis inside `<p>` and `<td>` tags.
-->

===== BODY PARAGRAPH SEQUENCE (demand notice) =====

After the stationery / RPAD banner / dated line / recipient / sender /
SUBJECT / opener (per BASELINE), emit numbered `<p>` paragraphs in this
order. Each `<p>` carries `style="padding:0 3.5rem;"`. Number 1, 2,
3, ...

**SUBJECT line - one line, 25 words max.** Pattern:

  <p style="padding:0 3.5rem;margin:1.25rem 0 0;"><strong>SUBJECT: Demand Notice - Recovery of <strong>Rs. [Amount]/- (Rupees [Words] Only)</strong> due under [short instrument reference, e.g. "Master Services Agreement dated 04/01/2025"].</strong></p>

Do NOT recite the contractual interest rate, the consequences, or the
list of statutory provisions in the SUBJECT - those go in the body.

**Paragraph 1 - Client identity and the commercial relationship.**
Full name (in `<strong>`), age, occupation / business, address. Then
identify the addressee's relationship to my client (purchaser, borrower,
contracting party, employer, lessee). Name the SPECIFIC INSTRUMENT or
contract that records the obligation: "the Purchase Order dated
DD/MM/YYYY bearing No. ......", "the Loan Agreement executed on
DD/MM/YYYY", "the Master Services Agreement dated DD/MM/YYYY".

**Paragraph 2 - The transaction in detail.**
Goods supplied / services rendered / amounts advanced - what, when,
where. Each item with its date and amount in figures + words (Indian
numbering: `Rs. 4,25,000/- (Rupees Four Lakh Twenty-Five Thousand
Only)`). Reference the supporting documents (invoices, delivery
challans, bank statements, ledger extracts, written confirmations) that
my client holds.

**Paragraph 3 - Particulars of dues.**
This paragraph CONTAINS a borderless 2-column HTML particulars table
when there are 2 or more line items. Single-item demands may use prose
instead. Pattern:

  <p style="padding:0 3.5rem;">3. The particulars of the amount
  due and outstanding from you to my client are as follows:</p>

  <table style="width:100%;border-collapse:collapse;margin:0.5rem auto;
  max-width:640px;">
  <thead>
  <tr>
  <th style="border:1px solid #cbd5e1;padding:8px 12px;text-align:left;">Particulars</th>
  <th style="border:1px solid #cbd5e1;padding:8px 12px;text-align:right;">Amount (Rs.)</th>
  </tr>
  </thead>
  <tbody>
  <tr>
  <td style="border:1px solid #cbd5e1;padding:8px 12px;">Principal sum due under [instrument], as on DD/MM/YYYY</td>
  <td style="border:1px solid #cbd5e1;padding:8px 12px;text-align:right;">[principal in figures]</td>
  </tr>
  <tr>
  <td style="border:1px solid #cbd5e1;padding:8px 12px;">Interest @ [rate]% p.a. from DD/MM/YYYY to DD/MM/YYYY ([N] days)</td>
  <td style="border:1px solid #cbd5e1;padding:8px 12px;text-align:right;">[interest in figures]</td>
  </tr>
  <tr>
  <td style="border:1px solid #cbd5e1;padding:8px 12px;">Notice costs and incidental expenses</td>
  <td style="border:1px solid #cbd5e1;padding:8px 12px;text-align:right;">[notice costs]</td>
  </tr>
  <tr>
  <td style="border:1px solid #cbd5e1;padding:8px 12px;"><strong>Total amount due</strong></td>
  <td style="border:1px solid #cbd5e1;padding:8px 12px;text-align:right;"><strong>[total in figures]</strong></td>
  </tr>
  </tbody>
  </table>

  <p style="padding:0 3.5rem;">that is to say, a total sum of
  <strong>Rs. [total]/- (Rupees [total in words] Only)</strong> as on
  DD/MM/YYYY, together with further interest @ [rate]% per annum from
  DD/MM/YYYY until the date of actual payment.</p>

**Paragraph 4 - Interest computation basis.**
State the contractual interest rate if the underlying instrument
specifies one; otherwise, claim Section 34 CPC commercial interest rate
(or RBI-prescribed rate for commercial transactions under the MSME Act
where applicable). One paragraph.

**Paragraph 5 - Default and prior demands.**
The dates on which the principal fell due, every prior demand /
reminder / email / WhatsApp message my client has sent (with dates and
mode), and the addressee's silence / refusal / partial payment in
response. State expressly that the addressee has had ample opportunity
to settle the dues.

**Paragraph 6 - Legal characterisation.**
Inline statement of the cause of action under Indian law:

  "Your aforesaid acts of non-payment constitute a clear and continuing
  breach of contract under <strong>Section 73 of the Indian Contract
  Act, 1872</strong>, entitling my client to recover the entire
  outstanding sum together with interest, costs, and damages. Should
  the underlying conduct also disclose dishonest inducement at the time
  of the original transaction, the same would amount to <strong>cheating
  under Section 318 of the Bharatiya Nyaya Sanhita, 2023</strong>
  (corresponding to Section 420 of the Indian Penal Code, 1860), and to
  <strong>criminal breach of trust under Section 316 BNS</strong>
  (corresponding to Section 406 IPC), each independently actionable."

Add or strip the criminal characterisation depending on the input - if
the default is a pure commercial dispute with no fraudulent element,
omit IPC / BNS. If the input shows induced advance payment based on
false representations, retain it.

**Paragraph 7 - DEMAND.**
Inline `<strong>TAKE NOTICE</strong>` opener. Required actions:

  (a) Pay to my client the total sum of <strong>Rs. [total]/- (Rupees
  [total in words] Only)</strong> together with further interest @
  [rate]% per annum from DD/MM/YYYY until the date of actual payment;
  (b) Pay notice costs of <strong>Rs. [notice costs]/-</strong>;
  (c) Confirm the payment in writing to my client and to the
  undersigned within the same period.

Time limit: <strong>15 days</strong> from receipt of this notice.

**Paragraph 8 - CONSEQUENCES.**
Inline `<strong>TAKE FURTHER NOTICE</strong>` opener. Specific
proceedings my client will initiate on non-compliance:

  - A <strong>summary suit under Order XXXVII of the Code of Civil
    Procedure, 1908</strong> for recovery of the full outstanding
    amount with interest at the contractual / statutory rate, before
    the [court name] at [City] - WHERE the cause is on a written
    contract / promissory note / bill of exchange (Order XXXVII applies
    only to such instruments). For other commercial recoveries, write
    "a regular civil suit for recovery of money".
  - For commercial disputes valued above Rs. 3,00,000/-, a
    <strong>Section 12A pre-institution mediation reference under the
    Commercial Courts Act, 2015</strong> may apply prior to suit;
    nothing in this notice waives any procedural right.
  - Where the agreement contains an arbitration clause, my client
    expressly reserves the right to invoke arbitration under the
    <strong>Arbitration and Conciliation Act, 1996</strong> in lieu of
    or in addition to the suit above.
  - Where the facts disclose dishonest inducement / criminal breach of
    trust, a complaint under <strong>Sections 316 / 318 of the
    Bharatiya Nyaya Sanhita, 2023</strong> before the concerned
    Magistrate / Police Station.
  - All costs, damages, interest, and incidental expenses recoverable
    in the above proceedings shall be borne by you.

Pick the proceedings that fit the facts - do not list every option when
only one or two apply.

**Paragraph 9 - Reservation of rights.**
This notice is issued without prejudice to any other rights, remedies,
and contentions of my client, all of which are expressly reserved.

**Paragraph 10 - Governing law and jurisdiction.**
Name the court / tribunal at <strong>[City]</strong> with exclusive
jurisdiction (typically the place where the contract was performed or
the cause of action arose).

After the body, emit the SIGNATURE BLOCK and Copy-to / Enclosures /
Mode of Service blocks per the BASELINE.
===== END BODY PARAGRAPH SEQUENCE =====
