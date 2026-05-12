<!--
STRUCTURAL REFERENCE for a Cheque Bounce Notice under Section 138 of the
Negotiable Instruments Act, 1881 - the statutory pre-condition to a criminal
complaint before the competent Magistrate.

How to use this reference:
1. Substitute every `[Bracketed Field]` with values from STRUCTURED INPUT and
   REFERENCE DOCUMENTS CONTEXT.
2. A bracket survives in your final output ONLY when the value is absent from
   BOTH sources. Use clear, advocate-editable labels like `[Cheque Number]`,
   `[Drawee Bank Branch]`. Never emit `[XX]`, `_____`, `XXXX`, `[NOT PROVIDED]`.
3. Do not invent values. Do not silently drop a line because data is missing —
   keep the line and bracket the missing field.
4. The stationery + RPAD banner + Dated line + recipient/sender/subject blocks
   are defined in the NOTICE BASELINE embedded in the system prompt. Start
   directly with the numbered body paragraphs below.
5. Use `<strong>` (NOT markdown `**`) for emphasis inside `<p>` and `<td>` tags.
6. The 30-day dispatch window (from receipt of Cheque Return Memo) and the
   15-day demand period are statutory and non-negotiable.
-->

===== BODY PARAGRAPH SEQUENCE (Section 138 NI Act notice) =====

After the stationery / RPAD banner / dated line / recipient / sender /
SUBJECT / opener (per BASELINE), emit numbered `<p>` paragraphs in this
order. Each `<p>` carries `style="padding:0 3.5rem;"`.

The SUBJECT line - one line, 25 words max. Pattern:

  <p style="padding:0 3.5rem;margin:1.25rem 0 0;"><strong>SUBJECT: Statutory Notice u/s 138 NI Act, 1881 - Cheque No. [Cheque No.] dated DD/MM/YYYY for <strong>Rs. [Amount]/-</strong>, dishonoured.</strong></p>

The full statutory regime, demand period, and consequences belong in the
body, not in the subject.

**Paragraph 1 - Client identity, addressee identity, underlying
relationship.**
My client's full name (in `<strong>`), age, occupation, address.
Addressee's full name and address (the drawer of the cheque). Then a
single sentence stating the legally enforceable debt or liability for
which the cheque was issued: "in discharge of the amount due under
[loan / advance / unpaid invoice / supply contract / lease deposit /
other lawful liability] dated DD/MM/YYYY, in the sum of
<strong>Rs. [Amount]/-</strong> as on DD/MM/YYYY".

**Paragraph 2 - Particulars of the cheque (mandatory).**
This paragraph contains a borderless 2-column HTML particulars table.
Pattern:

  <p style="padding:0 3.5rem;">2. The particulars of the cheque issued
  by you to my client in discharge of the aforesaid liability are as
  follows:</p>

  <table style="width:100%;border-collapse:collapse;margin:0.5rem auto;
  max-width:640px;">
  <tbody>
  <tr><td style="border:1px solid #cbd5e1;padding:8px 12px;width:40%;">Cheque Number</td><td style="border:1px solid #cbd5e1;padding:8px 12px;"><strong>[Cheque No.]</strong></td></tr>
  <tr><td style="border:1px solid #cbd5e1;padding:8px 12px;">Date of cheque</td><td style="border:1px solid #cbd5e1;padding:8px 12px;"><strong>DD/MM/YYYY</strong></td></tr>
  <tr><td style="border:1px solid #cbd5e1;padding:8px 12px;">Amount</td><td style="border:1px solid #cbd5e1;padding:8px 12px;"><strong>Rs. [Amount]/- (Rupees [Amount in Words] Only)</strong></td></tr>
  <tr><td style="border:1px solid #cbd5e1;padding:8px 12px;">Drawer (you)</td><td style="border:1px solid #cbd5e1;padding:8px 12px;">[Drawer Name and account holding capacity]</td></tr>
  <tr><td style="border:1px solid #cbd5e1;padding:8px 12px;">Drawee bank and branch</td><td style="border:1px solid #cbd5e1;padding:8px 12px;">[Bank Name], [Branch Name and Address]</td></tr>
  <tr><td style="border:1px solid #cbd5e1;padding:8px 12px;">Drawer's account number</td><td style="border:1px solid #cbd5e1;padding:8px 12px;">[Account No. (mask all but last four if input exceeds last four)]</td></tr>
  <tr><td style="border:1px solid #cbd5e1;padding:8px 12px;">Payee</td><td style="border:1px solid #cbd5e1;padding:8px 12px;">[Client Name]</td></tr>
  </tbody>
  </table>

**Paragraph 3 - Presentation and dishonour.**
The date my client deposited / presented the cheque, the bank where it
was presented (name and branch), and the date the cheque was returned
unpaid by the drawee bank. Cite the dishonour memo: "the said cheque was
returned unpaid by [Drawee Bank], [Branch], vide Cheque Return Memo
dated DD/MM/YYYY, with the endorsement
<strong>'[exact reason from memo - e.g., FUNDS INSUFFICIENT /
ACCOUNT CLOSED / PAYMENT STOPPED BY DRAWER / EXCEEDS ARRANGEMENT /
SIGNATURE DIFFERS / REFER TO DRAWER]'</strong>".

State the date my client RECEIVED the dishonour memo - this is Day 0
for the §138 proviso (b) 30-day window.

**Paragraph 4 - Underlying legally enforceable debt (mandatory).**
A standalone paragraph reciting the legally enforceable debt or
liability with documentary references. State expressly that the cheque
was issued, drawn on an account maintained by you, in discharge in
whole or in part of the said legally enforceable debt or other
liability - the magic words from §138.

**Paragraph 5 - Statutory characterisation.**
"Your aforesaid act of issuing the dishonoured cheque, knowing or
having reason to believe that the same would not be honoured upon
presentation, constitutes an offence punishable under <strong>Section
138 of the Negotiable Instruments Act, 1881</strong>, attracting a
sentence of imprisonment which may extend to two years, or fine which
may extend to twice the amount of the cheque, or both. The presumption
under <strong>Section 139 of the said Act</strong> is squarely available
to my client. The territorial jurisdiction for prosecution lies under
<strong>Section 142(2) of the said Act</strong> before the competent
Magistrate having jurisdiction over the bank branch on which the
cheque was drawn / where the cheque was presented for collection."

**Paragraph 6 - Demand (statutory mandatory).**
Inline `<strong>TAKE NOTICE</strong>` opener. The demand MUST require:

  Payment of the entire cheque amount of <strong>Rs. [Amount]/- (Rupees
  [Amount in Words] Only)</strong>, by way of demand draft / RTGS / NEFT
  in favour of <strong>[Client Name]</strong> (account particulars
  available on request from the undersigned), within <strong>15 (fifteen)
  days</strong> of the receipt of this notice, in terms of and as
  prescribed by the proviso (c) to Section 138 of the Negotiable
  Instruments Act, 1881.

The 15-day limit is statutory - do NOT shorten or extend.

**Paragraph 7 - Notice costs and incidental relief.**
A separate `<p>`: payment of notice costs assessed at
<strong>Rs. [notice costs]/-</strong> and any further interest, costs,
or charges assessed by the Court in the event of prosecution.

**Paragraph 8 - Consequences (TAKE FURTHER NOTICE).**
Inline `<strong>TAKE FURTHER NOTICE</strong>` opener:

  Should you fail to make payment of the said sum of
  <strong>Rs. [Amount]/-</strong> within 15 (fifteen) days of receipt
  of this notice, my client shall be constrained to file a Criminal
  Complaint against you under <strong>Section 138 read with Section 142
  of the Negotiable Instruments Act, 1881</strong>, before the
  competent Magistrate at <strong>[City]</strong> within 30 days of
  the expiry of the aforesaid 15-day period - and shall further pursue
  all civil remedies for recovery of the cheque amount along with
  interest, damages, and costs, before the appropriate civil court /
  forum, all entirely at your risk and cost.

**Paragraph 9 - Reservation of rights.**
"This notice is issued without prejudice to any other rights, remedies,
and contentions of my client - civil, criminal, or otherwise - all of
which are expressly reserved."

**Paragraph 10 - Governing law and jurisdiction.**
"This notice is governed by the laws of the Republic of India, and any
proceedings arising from or in connection herewith shall lie before
the courts at <strong>[City]</strong>, having regard to Section
142(2) of the Negotiable Instruments Act, 1881."

After the body, emit the SIGNATURE BLOCK and Copy-to / Enclosures /
Mode of Service blocks per the BASELINE.

The Enclosures list MUST mention:
  1. Photocopy of the dishonoured cheque bearing No. [Cheque No.]
     dated DD/MM/YYYY.
  2. Photocopy of the Cheque Return Memo dated DD/MM/YYYY issued by
     [Drawee Bank].
  3. [Optionally] copy of the underlying agreement / invoice / loan
     receipt evidencing the legally enforceable debt.

Mode of Service MUST be Registered Post Acknowledgement Due (R.P.A.D.)
- this is the strongest proof of dispatch and receipt for §138
purposes; Speed Post is acceptable as a fallback. Add a follow-up via
email / WhatsApp at the addressee's known address only as
SUPPLEMENTARY service.
===== END BODY PARAGRAPH SEQUENCE =====
