"""Custom drafting agent driven by a user-stored template prompt."""

from legal_agent.agents.drafts.base import BaseDraftingAgent

# Minimal guardrails applied to ALL custom template drafts regardless of user prompt content.
# These are non-negotiable safety rules — the user's generated_prompt sits after these.
CUSTOM_GUARDRAILS = """You are an expert legal drafting assistant for Indian law.

STRICT RULES — these override everything:
1. NEVER use placeholder text: [Name], [Date], _____, XXXX are forbidden
2. Output clean markdown ONLY — no HTML tags, no code fences
3. Use actual names, dates, and details from the user's input
4. If specific information is missing, use contextual alternatives (e.g. "the applicant", "the said property")
5. The document must be COMPLETE and ready to use as-is
6. Follow the template structure from your instructions EXACTLY — every required section must appear"""


class CustomDraftingAgent(BaseDraftingAgent):
    """Agent that drafts documents following a user-defined template prompt.

    Unlike system agents (where system_prompt is a class attribute), this agent
    takes the user's stored generated_prompt at construction time and sets it as
    an instance attribute — so each instance is isolated to one template.
    """

    def __init__(self, model: str, provider: str, user_prompt: str):
        super().__init__(model, provider)
        # Instance-level — not a class constant like BailApplicationAgent.system_prompt
        self.system_prompt = CUSTOM_GUARDRAILS + "\n\n" + user_prompt
