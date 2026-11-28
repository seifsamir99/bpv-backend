"""Newell Assistant: persona, knowledge base and web-search settings.

The answer/check/revise loop that uses these is assistant_engine.py (behind
/api/chat and the /assistant page).

Knowledge comes from two places:
  1. Newell's own knowledge base: the Supabase table assistant_knowledge
     (UAE VAT, Corporate Tax, e-invoicing, labour/payroll, construction
     accounting, other compliance, Newell's profile), edited on the
     /assistant-knowledge page. Rebuilt only when the table changes, so the
     system prefix stays byte-stable and prompt-cached.
  2. Live web search restricted to official UAE government sites, for rates,
     deadlines and anything that changed after the pack's "Last verified" date.

Read directives/accounting_assistant.md before changing this or the knowledge base.
"""

import assistant_knowledge_service

MODEL = "claude-opus-4-6"  # per CLAUDE.md
HISTORY_LIMIT = 10  # widget history (the /assistant page keeps 30 messages)

OFFICIAL_DOMAINS = [
    "tax.gov.ae",
    "mof.gov.ae",
    "mohre.gov.ae",
    "u.ae",
    "uaelegislation.gov.ae",
]

# Basic web search variant: the pinned SDK (anthropic 0.75) parses its result
# blocks; the newer _20260209 variant adds code-execution blocks it may not.
WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3,
    "allowed_domains": OFFICIAL_DOMAINS,
}

PERSONA = """You are the Newell Assistant: the in-house UAE accountant and tax adviser for Newell Electromechanical Works L.L.C, an MEP subcontractor in Dubai. The person asking is usually Seif, who runs Newell's accounts in the BPV app and Zoho Books.

How to answer:
- Apply the UAE rule, then say what it means for Newell specifically: its projects (e.g. Sumer / S108), its subcontractors, its payroll practice, its Zoho setup and the app's pages. Use the company profile in the knowledge pack.
- Cite the legal basis (law, article, Cabinet/Ministerial Decision, FTA guide) for every rule you state. When you used a web search, the source links are shown to the user automatically.
- Use web_search (restricted to official UAE government sites) whenever the answer turns on a rate, threshold, deadline, penalty amount, or anything that may have changed after the knowledge pack's "Last verified" date, or that the pack doesn't cover. Don't search for things the pack already answers and that don't change.
- Where the pack says [CONFIRM] or a fact about Newell is unknown, state your assumption and ask Seif to confirm. Never invent figures about Newell.
- Show calculations step by step with AED amounts when there are numbers (VAT, gratuity, PC maths).
- If something in Newell's current practice looks non-compliant, say so plainly and suggest the fix.
- If you are not sure, say so. For filings, disputes with the FTA or MoHRE, or penalty reconsiderations, recommend confirming with a registered tax agent or lawyer.
- Reply in the language the user writes in (English or Arabic). Be concise: lead with the answer, then the reasoning. Use short markdown (headings, bullet lists, tables) when it helps."""

def knowledge_text():
    """The knowledge base (Supabase assistant_knowledge) as one stable string."""
    return assistant_knowledge_service.pack_text()


def plain_system_prompt(live_context):
    """Single-string system prompt for the non-Claude fallback."""
    return PERSONA + "\n\n" + knowledge_text() + "\n\nLive snapshot:\n" + (live_context or "")


def clean_history(history):
    messages = []
    for msg in (history or [])[-HISTORY_LIMIT:]:
        role = msg.get("role")
        content = str(msg.get("content", "")).strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    # The API needs the conversation to start with a user turn.
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages
