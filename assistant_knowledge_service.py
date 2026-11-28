"""The Newell Assistant's own knowledge base (Supabase table assistant_knowledge).

Edited by the office on /assistant-knowledge; read by accounting_assistant.py
on every chat question. The pack text is cached in memory and rebuilt only when
the table changes (row count or latest updated_at), so an edit applies to the
next question while the prompt prefix stays byte-stable (prompt-cached) between
edits. Table created/seeded by execution/migrate_assistant_knowledge.py.
"""

import logging
import threading

import psycopg2

import db

log = logging.getLogger(__name__)

# Topic display order (any topic not listed sorts after these, alphabetically).
TOPIC_ORDER = [
    "Newell Profile",
    "VAT",
    "Corporate Tax",
    "E-invoicing",
    "Labour & Payroll",
    "Construction Accounting",
    "Other Compliance",
]

MAX_TITLE = 200
MAX_TOPIC = 80
MAX_BODY = 20000

_COLUMNS = "id, topic, title, body, sources, last_verified, sort_order, active, updated_at, updated_by"

_lock = threading.Lock()
_cache = {"stamp": None, "text": None}


class KnowledgeError(ValueError):
    """A validation problem the user can fix (shown as-is)."""


def _topic_key(topic):
    return (TOPIC_ORDER.index(topic) if topic in TOPIC_ORDER else len(TOPIC_ORDER), topic.lower())


def _json(row):
    return {
        "id": row["id"],
        "topic": row["topic"],
        "title": row["title"],
        "body": row["body"],
        "sources": row["sources"],
        "lastVerified": row["last_verified"].isoformat() if row["last_verified"] else None,
        "sortOrder": row["sort_order"],
        "active": row["active"],
        "updatedAt": row["updated_at"].isoformat() if row["updated_at"] else None,
        "updatedBy": row.get("updated_by") or "office",  # 'assistant' when the self-update changed it
    }


def _sorted(rows):
    return sorted(rows, key=lambda r: (_topic_key(r["topic"]), r["sort_order"], r["id"]))


def list_entries():
    rows = db.q(f"SELECT {_COLUMNS} FROM assistant_knowledge")
    entries = [_json(r) for r in _sorted(rows)]
    topics = sorted({e["topic"] for e in entries}, key=_topic_key)
    return {"entries": entries, "topics": topics}


def _clean(data, partial=False):
    out = {}
    for field, limit in (("topic", MAX_TOPIC), ("title", MAX_TITLE)):
        if field in data or not partial:
            value = str(data.get(field) or "").strip()
            if not value:
                raise KnowledgeError(f"{field.capitalize()} is required")
            if len(value) > limit:
                raise KnowledgeError(f"{field.capitalize()} is too long (max {limit} characters)")
            out[field] = value
    if "body" in data or not partial:
        body = str(data.get("body") or "").strip()
        if not body:
            raise KnowledgeError("The entry text is empty")
        if len(body) > MAX_BODY:
            raise KnowledgeError(f"The entry text is too long (max {MAX_BODY} characters)")
        out["body"] = body
    if "sources" in data or not partial:
        out["sources"] = str(data.get("sources") or "").strip()
    if "lastVerified" in data or not partial:
        out["last_verified"] = data.get("lastVerified") or None
    if "sortOrder" in data:
        try:
            out["sort_order"] = int(data.get("sortOrder") or 0)
        except (TypeError, ValueError):
            raise KnowledgeError("Order must be a whole number")
    if "active" in data:
        out["active"] = bool(data.get("active"))
    return out


def _duplicate(exc):
    return isinstance(exc, psycopg2.errors.UniqueViolation) or "assistant_knowledge_topic_title_key" in str(exc)


def create_entry(data, by="office"):
    values = _clean(data)
    values["updated_by"] = by
    if "sort_order" not in values:
        row = db.q_one(
            "SELECT coalesce(max(sort_order), -1) + 1 AS n FROM assistant_knowledge WHERE topic = %s",
            (values["topic"],))
        values["sort_order"] = row["n"]
    cols = list(values)
    try:
        row = db.q_one(
            f"INSERT INTO assistant_knowledge ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
            f"RETURNING {_COLUMNS}",
            [values[c] for c in cols])
    except Exception as exc:
        if _duplicate(exc):
            raise KnowledgeError(f'"{values["title"]}" already exists under {values["topic"]}')
        raise
    return _json(row)


def get_entry(entry_id):
    row = db.q_one(f"SELECT {_COLUMNS} FROM assistant_knowledge WHERE id = %s", (entry_id,))
    return _json(row) if row else None


def update_entry(entry_id, data, by="office"):
    """by: 'office' (the /assistant-knowledge page) or 'assistant' (assistant_updater.py)."""
    values = _clean(data, partial=True)
    if not values:
        raise KnowledgeError("Nothing to update")
    values["updated_by"] = by
    sets = ", ".join(f"{c} = %s" for c in values) + ", updated_at = now()"
    try:
        row = db.q_one(
            f"UPDATE assistant_knowledge SET {sets} WHERE id = %s RETURNING {_COLUMNS}",
            [*values.values(), entry_id])
    except Exception as exc:
        if _duplicate(exc):
            raise KnowledgeError("Another entry with that topic and title already exists")
        raise
    if not row:
        raise KnowledgeError("Entry not found")
    return _json(row)


def delete_entry(entry_id):
    if not db.execute("DELETE FROM assistant_knowledge WHERE id = %s", (entry_id,)):
        raise KnowledgeError("Entry not found")


def _format(rows):
    parts, current = [], None
    for r in _sorted(rows):
        if r["topic"] != current:
            if current is not None:
                parts.append("</topic>")
            current = r["topic"]
            parts.append(f'<topic name="{current}">')
        verified = r["last_verified"].isoformat() if r["last_verified"] else "unknown"
        entry = f"## {r['title']}\n(Last verified: {verified})\n{r['body']}"
        if r["sources"]:
            entry += f"\nSources: {r['sources']}"
        parts.append(entry)
    if current is not None:
        parts.append("</topic>")
    return "<knowledge_pack>\n" + "\n\n".join(parts) + "\n</knowledge_pack>"


def pack_text():
    """Active entries as one stable string, rebuilt only when the table changed.

    If the database can't be reached, the last good copy is used (or an empty
    pack before the first load) so the assistant still answers.
    """
    try:
        stamp_row = db.q_one(
            "SELECT count(*) AS n, max(updated_at) AS latest FROM assistant_knowledge WHERE active")
        stamp = (stamp_row["n"], stamp_row["latest"])
        with _lock:
            if _cache["stamp"] == stamp and _cache["text"] is not None:
                return _cache["text"]
        rows = db.q(f"SELECT {_COLUMNS} FROM assistant_knowledge WHERE active")
        text = _format(rows)
        with _lock:
            _cache.update(stamp=stamp, text=text)
        return text
    except Exception:
        log.exception("assistant_knowledge: could not load the knowledge base")
        with _lock:
            return _cache["text"] or "<knowledge_pack>\n(knowledge base unavailable)\n</knowledge_pack>"
