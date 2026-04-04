"""Generate a structured drafting prompt from extracted template text using an LLM."""

import logging

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

_ANALYZER_SYSTEM_PROMPT = """You are a legal document template analyst.

Your job: given a sample legal document, produce a detailed DRAFTING PROMPT that instructs
an AI drafting agent to reproduce documents in exactly the same style, structure, and format.

The drafting prompt you output will be stored and used as the agent's system instructions
every time the user asks to draft a new document using this template.

Analyze the template carefully and capture ALL of the following in your output:

1. DOCUMENT STRUCTURE
   - List every section in the exact order it appears (e.g., cause title, facts, grounds, prayer)
   - Include sub-sections and their numbering style (1.1, (a), Roman numerals, etc.)

2. FORMATTING RULES
   - Header style (centered, bold, ALL CAPS, etc.)
   - How parties are introduced and referred to throughout
   - Paragraph numbering style
   - Use of horizontal rules / dividers between sections
   - Table formats if any (markdown pipe tables)

3. LANGUAGE & TONE
   - Formal legal language patterns unique to this template
   - Specific phrases or opening/closing formulas used
   - How the document addresses the court / recipient
   - Any bilingual (Hindi + English) patterns if present

4. LEGAL REFERENCES
   - Which acts / sections are cited and how
   - Citation format used (SCC, AIR, etc.)
   - Whether case law is cited inline or in a table

5. PARTY DETAILS FORMAT
   - Exactly how party blocks are formatted (name, age, address, occupation layout)
   - Labels used (Applicant/Non-Applicant, Plaintiff/Defendant, etc.)

OUTPUT FORMAT:
Write the drafting prompt directly — start with "You are an expert legal drafting assistant..."
Be specific and prescriptive. Use numbered rules and section headings.
Include a TEMPLATE STRUCTURE section with the exact section names in order.
Do NOT include analysis commentary — write only the prompt itself.
The prompt should be 400-800 words."""

_ANALYZER_HUMAN_TEMPLATE = """Here is the template document to analyze:

=== TEMPLATE START ===
{text}
=== TEMPLATE END ===

Generate the drafting prompt for this template."""


async def generate_template_prompt(text: str, model: str) -> str:
    """Analyze extracted template text and return a drafting prompt string.

    Args:
        text: Extracted plain text from the user's template file.
        model: LangChain model string, e.g. 'openai:gpt-4o-mini'.

    Returns:
        A drafting prompt string to store in the DB and use as the agent's system prompt.
    """
    try:
        provider, model_name = model.split(":", 1)
        provider_map = {"openai": "openai", "anthropic": "anthropic", "gemini": "google-genai"}
        llm = init_chat_model(model_name, model_provider=provider_map.get(provider, provider))

        response = await llm.ainvoke([
            SystemMessage(content=_ANALYZER_SYSTEM_PROMPT),
            HumanMessage(content=_ANALYZER_HUMAN_TEMPLATE.format(text=text[:12000])),
        ])
        prompt = str(response.content).strip()
        logger.info(f"Generated template prompt: {len(prompt)} chars")
        return prompt

    except Exception:
        logger.exception("Template prompt generation failed")
        raise
