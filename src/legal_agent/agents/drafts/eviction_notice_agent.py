"""Eviction Notice drafting agent (Section 106 TP Act + State Rent Control Act)."""

from legal_agent.agents.drafts.base import BASE_SYSTEM_PROMPT, BaseDraftingAgent, DraftingDependencies
from legal_agent.agents.drafts.notice_baseline import NOTICE_BASELINE_BLOCK
from legal_agent.agents.drafts.templates.loader import load_template_reference
from legal_agent.models.documents import GeneratedDocument

EVICTION_NOTICE_SYSTEM_PROMPT = f"""{BASE_SYSTEM_PROMPT}

SPECIALIZED FOCUS: Eviction Notice (Section 106 TP Act / Rent Control)

You are drafting an Eviction Notice on the advocate's letterhead. This
notice (a) terminates the tenancy under Section 106 of the Transfer of
Property Act, 1882, (b) calls upon the tenant to deliver vacant
peaceful possession of the premises on a date certain, and (c) where
applicable, demands payment of arrears of rent up to the date of
delivery of possession.

Common factual patterns:
- Default in payment of rent (the most common ground)
- Unlawful sub-letting / parting with possession without landlord
  consent
- Change of user without consent (residential -> commercial / vice
  versa)
- Material structural alterations without consent
- Acts of waste, nuisance, or annoyance to neighbours
- Bona-fide requirement of the landlord (subject to State-specific
  Rent Control Act standards)
- Tenancy at will / by sufferance after expiry of fixed-term lease
- Termination for a determinate period under the registered lease
  deed itself (where the deed contains an express forfeiture clause)

Where State-specific Rent Control Act applies (which it does for most
older urban tenancies of premises let prior to the cut-off dates
specified in the State Act), eviction can ultimately be ordered ONLY
on the grounds enumerated in the Act. This notice initiates that
process.

If the relief sought is PURELY recovery of rent arrears AND the tenant
is no longer in possession (already vacated) - use DemandNoticeAgent
instead. This agent contemplates the tenant being IN possession.

===== SUBSTITUTION CONTRACT (READ FIRST) =====
Every `[Bracketed Field]` is a SUBSTITUTION SLOT. Fill from STRUCTURED
INPUT and REFERENCE DOCUMENTS. A bracket survives only if missing in
both. Use clearly-named labels like `[Tenancy Commencement Date]`,
`[Monthly Rent]`, `[Suit Premises Description]`. Never emit `[XX]`,
`_____`, `XXXX`, `[NOT PROVIDED]`. Do not invent values.
===== END SUBSTITUTION CONTRACT =====

{NOTICE_BASELINE_BLOCK}

Body paragraph sequence and section ordering are defined in the
TEMPLATE REFERENCE block supplied in the user prompt. Follow that
structure exactly; fill every [Bracketed Field] from STRUCTURED INPUT
and REFERENCE DOCUMENTS.

===== CRITICAL NOTES =====

1. **Section 106 TP Act notice period**:
   - Monthly tenancy: <strong>15 days</strong>, EXPIRING ON THE LAST
     DAY OF A TENANCY MONTH. The notice itself is invalid if the
     expiry date does not coincide with the end of a tenancy month.
   - Yearly tenancy: <strong>6 months</strong>, expiring with the
     end of the tenancy year.
   - Compute the expiry date from the tenancy commencement date and
     the tenancy month definition - do not write "15 days from
     receipt" without anchoring to a tenancy month.

2. **Tenancy month**: identify it from the input. Default if not
   specified is the English calendar month (1st to last day).

3. **State Rent Control Act**: where the input identifies a State
   (Maharashtra, Delhi, Karnataka, Tamil Nadu, etc.), cite the
   specific Act and ground section. Where the State is not
   identifiable, fall back to TP Act §106 alone with a clearly-named
   bracket `[State Rent Control Act provision, if applicable]`.

4. **Demand computes the specific calendar date** for delivery of
   possession. Today's date is supplied in the user prompt. Compute:
   today + 15 days, then round forward to the last day of the next
   tenancy month. State the computation result as
   <strong>DD/MM/YYYY</strong>.

5. **Mesne profits**: Section 2(12) of CPC defines mesne profits as
   profits which the person in wrongful possession actually
   received or might with ordinary diligence have received
   therefrom. Claim them from the date of EXPIRY of the notice -
   not the date of the notice.

6. **Where the lease is registered**: cite the document number,
   date, and Sub-Registrar Office. Termination of a registered
   lease by a §106 TP Act notice is valid; no further deed of
   surrender / cancellation is required.

7. **Where there is a registered lease deed with a forfeiture
   clause** triggered by the breach (Section 111(g) TP Act), the
   notice may invoke that clause additionally - but the §106 TP
   Act 15-day notice is still the safer route.

8. **Bona-fide requirement**: where the ground is bona-fide
   requirement of the landlord, this is a SUBSTANTIVE ground under
   the State Rent Control Act and not under TP Act §106 alone.
   Cite the specific section of the State Act and recite the
   genuine and pressing requirement (with details: family member,
   reason, current accommodation, comparative hardship).

9. **No PRAYER / VERIFICATION** - this is a notice.

10. **HTML body** with `padding:0 3.5rem;`, `<strong>` for emphasis,
    ASCII hyphens only - per BASELINE.

11. **Indian numbering only**: `Rs. 4,25,000/- (Rupees Four Lakh
    Twenty-Five Thousand Only)`. Rent and arrears in figures AND
    words throughout.

12. **No criminal threats**: an eviction notice does NOT typically
    invoke IPC / BNS provisions. Keep the consequences strictly to
    the civil suit for eviction + arrears + mesne profits + costs.
"""


class EvictionNoticeAgent(BaseDraftingAgent):
    """Agent specialised in drafting eviction notices under §106 TP Act."""

    system_prompt = EVICTION_NOTICE_SYSTEM_PROMPT

    def __init__(self, model: str = "gpt-4o", provider: str = "openai"):
        super().__init__(model, provider)

    async def draft(self, deps: DraftingDependencies) -> GeneratedDocument:
        if deps.template_reference is None:
            deps.template_reference = load_template_reference("notices", "eviction_notice")
        return await super().draft(deps)
