"""Google Legal Search tool using Gemini native grounding."""

import asyncio

from langchain_core.tools import tool


def create_google_legal_search_tool(api_key: str):
    @tool
    async def google_legal_search(query: str) -> str:
        """Search Indian supreme court and high court judgments via Google."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash",
            contents=f"Find relevant Indian court judgments, statutes, and legal precedents for: {query}",
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            ),
        )
        return response.text or "No results found."

    return google_legal_search
