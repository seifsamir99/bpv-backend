"""Saved chats for the /assistant page: conversations, messages and files.

Tables (execution/migrate_assistant_chat.py): assistant_conversations,
assistant_messages, assistant_files. The engine itself is assistant_engine.py;
this module stores things and turns stored turns back into API content blocks.
"""

import base64
import io
import json
import logging
import os
import threading

import db

log = logging.getLogger(__name__)

HISTORY_MESSAGES = 30
MAX_FILE_BYTES = 10 * 1024 * 1024   # per upload
MAX_FILES_PER_MESSAGE = 5
MAX_PDF_PAGES = 100
MAX_TEXT_CHARS = 400_000            # extracted text per file (~100k tokens)
# What one request may carry in re-sent files. The API caps a request at 32 MB
# and base64 adds a third, so older files past this budget are left out (the
# assistant is told they exist and can ask for them again).
SEND_BYTES_BUDGET = 20 * 1024 * 1024
SEND_TEXT_BUDGET = 600_000
IMAGE_MAX_EDGE = 2000
TITLE_LEN = 60

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}
TEXT_EXTS = {".csv", ".txt", ".xlsx", ".xls", ".md", ".json"}


class ChatError(Exception):
    """A problem with the request itself (shown to the user as-is)."""


class Busy(ChatError):
    """The conversation already has a question being answered."""


# ---------------------------------------------------------------------------
# One question at a time per conversation. Railway runs a single process, so an
# in-process set is enough, and it doesn't pin a pooled DB connection for the
# minutes an answer can take (an advisory lock would).
# ---------------------------------------------------------------------------
_running = set()
_running_lock = threading.Lock()


def try_start(conversation_id):
    with _running_lock:
        if conversation_id in _running:
            return False
        _running.add(conversation_id)
        return True


def finish(conversation_id):
    with _running_lock:
        _running.discard(conversation_id)


# ---------------------------------------------------------------------------
# JSON shapes
# ---------------------------------------------------------------------------

def _iso(ts):
    return ts.isoformat() if ts else None


def _conv_json(row):
    return {"id": row["id"], "title": row["title"], "createdAt": _iso(row.get("created_at")),
            "updatedAt": _iso(row.get("updated_at"))}


def _file_json(row):
    return {"id": row["id"], "filename": row["filename"], "mime": row["mime"],
            "sizeBytes": row["size_bytes"], "hasText": bool(row.get("has_text"))}


def _msg_json(row, files_by_id):
    file_ids = row.get("file_ids") or []
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "sources": row.get("sources") or [],
        "check": row.get("check_result"),
        "files": [files_by_id[f] for f in file_ids if f in files_by_id],
        "error": row.get("error", False),
        "createdAt": _iso(row.get("created_at")),
    }


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

def _clean_title(title):
    title = " ".join(str(title or "").split())
    return (title[:TITLE_LEN].rstrip() + "…") if len(title) > TITLE_LEN else (title or "New chat")


def list_conversations():
    rows = db.q("SELECT id, title, created_at, updated_at FROM assistant_conversations "
                "ORDER BY updated_at DESC LIMIT 500")
    return [_conv_json(r) for r in rows]


def create_conversation(title=None):
    row = db.q_one("INSERT INTO assistant_conversations (title) VALUES (%s) "
                   "RETURNING id, title, created_at, updated_at", (_clean_title(title),))
    return _conv_json(row)


def _require(conversation_id):
    row = db.q_one("SELECT id, title, created_at, updated_at FROM assistant_conversations WHERE id = %s",
                   (conversation_id,))
    if not row:
        raise ChatError("Conversation not found")
    return row


def get_conversation(conversation_id):
    conv = _require(conversation_id)
    files = db.q("SELECT id, filename, mime, size_bytes, text_content IS NOT NULL AS has_text "
                 "FROM assistant_files WHERE conversation_id = %s", (conversation_id,))
    files_by_id = {f["id"]: _file_json(f) for f in files}
    rows = db.q("SELECT id, role, content, sources, check_result, file_ids, error, created_at "
                "FROM assistant_messages WHERE conversation_id = %s ORDER BY id", (conversation_id,))
    return {"conversation": _conv_json(conv), "messages": [_msg_json(r, files_by_id) for r in rows]}


def rename_conversation(conversation_id, title):
    if not str(title or "").strip():
        raise ChatError("Title can't be empty")
    row = db.q_one("UPDATE assistant_conversations SET title = %s WHERE id = %s "
                   "RETURNING id, title, created_at, updated_at", (_clean_title(title), conversation_id))
    if not row:
        raise ChatError("Conversation not found")
    return _conv_json(row)


def delete_conversation(conversation_id):
    if conversation_id in _running:
        raise Busy("Wait for the current answer to finish before deleting this chat")
    if not db.execute("DELETE FROM assistant_conversations WHERE id = %s", (conversation_id,)):
        raise ChatError("Conversation not found")


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def _ext(filename):
    return os.path.splitext(filename or "")[1].lower()


def _docx_text(data):
    import docx  # python-docx
    document = docx.Document(io.BytesIO(data))
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for t, table in enumerate(document.tables, 1):
        parts.append(f"\n[Table {t}]")
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(parts)


def _shrink_image(data, filename, mime):
    """Downscale big photos/scans so they fit the API's image limits and cost less."""
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    if max(img.size) <= IMAGE_MAX_EDGE and len(data) <= 3_500_000:
        return data, filename, mime
    img.thumbnail((IMAGE_MAX_EDGE, IMAGE_MAX_EDGE))
    if img.mode not in ("RGB", "L"):
        background = Image.new("RGB", img.size, "white")
        background.paste(img, mask=img.convert("RGBA").split()[-1])
        img = background
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue(), os.path.splitext(filename)[0] + ".jpg", "image/jpeg"


def save_file(conversation_id, filename, mime, data, text_content=None):
    """Store an upload. Returns its metadata. Raises ChatError for anything we can't use."""
    _require(conversation_id)
    filename = os.path.basename(str(filename or "file")).strip() or "file"
    ext = _ext(filename)
    if not data:
        raise ChatError(f"{filename} is empty")
    if len(data) > MAX_FILE_BYTES:
        raise ChatError(f"{filename} is over {MAX_FILE_BYTES // (1024 * 1024)} MB. Split it or send a smaller scan.")

    if ext == ".pdf":
        mime, text_content = "application/pdf", None
        try:
            import fitz  # PyMuPDF
            with fitz.open(stream=data, filetype="pdf") as pdf:
                pages = pdf.page_count
        except Exception:
            raise ChatError(f"{filename} doesn't open as a PDF")
        if pages > MAX_PDF_PAGES:
            raise ChatError(f"{filename} has {pages} pages; the limit is {MAX_PDF_PAGES}. Send only the pages you need.")
    elif ext in IMAGE_TYPES:
        try:
            data, filename, mime = _shrink_image(data, filename, IMAGE_TYPES[ext])
        except Exception:
            raise ChatError(f"{filename} doesn't open as an image")
        text_content = None
    elif ext == ".docx":
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        try:
            text_content = _docx_text(data)
        except Exception:
            raise ChatError(f"{filename} doesn't open as a Word file")
    elif ext in TEXT_EXTS:
        # Excel is converted to CSV text in the browser; CSV/TXT can be decoded here.
        if not text_content and ext not in (".xlsx", ".xls"):
            text_content = data.decode("utf-8", errors="replace")
        if not text_content:
            raise ChatError(f"Couldn't read {filename}. Try saving it as CSV.")
        mime = mime or "text/plain"
    elif ext == ".doc":
        raise ChatError("Old .doc files can't be read. Save it as .docx or PDF.")
    else:
        raise ChatError(f"{filename}: this file type isn't supported. Use PDF, an image, Excel, CSV or Word.")

    if text_content is not None:
        text_content = text_content.strip()
        if not text_content:
            raise ChatError(f"{filename} has no readable text")
        if len(text_content) > MAX_TEXT_CHARS:
            raise ChatError(f"{filename} is too long as text ({len(text_content):,} characters, "
                            f"limit {MAX_TEXT_CHARS:,}). Send only the sheets or pages you need.")

    import psycopg2
    row = db.q_one(
        "INSERT INTO assistant_files (conversation_id, filename, mime, size_bytes, data, text_content) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "RETURNING id, filename, mime, size_bytes, text_content IS NOT NULL AS has_text",
        (conversation_id, filename, mime, len(data), psycopg2.Binary(data), text_content),
    )
    return _file_json(row)


def save_generated(conversation_id, filename, mime, data, text_content=None):
    """Store a document the assistant made (assistant_documents.render). text_content
    is the document's source text, kept on the Word file so later turns can edit it."""
    import psycopg2
    row = db.q_one(
        "INSERT INTO assistant_files (conversation_id, filename, mime, size_bytes, data, text_content) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "RETURNING id, filename, mime, size_bytes, text_content IS NOT NULL AS has_text",
        (conversation_id, filename, mime, len(data), psycopg2.Binary(data), text_content),
    )
    return _file_json(row)


def get_file(file_id):
    row = db.q_one("SELECT id, filename, mime, data FROM assistant_files WHERE id = %s", (file_id,))
    if not row:
        raise ChatError("File not found")
    row["data"] = bytes(row["data"])
    return row


def check_file_ids(conversation_id, file_ids):
    """Validate the files attached to a new question; returns them as a clean int list."""
    _require(conversation_id)
    try:
        ids = [int(f) for f in (file_ids or [])]
    except (TypeError, ValueError):
        raise ChatError("Bad file list")
    ids = list(dict.fromkeys(ids))
    if len(ids) > MAX_FILES_PER_MESSAGE:
        raise ChatError(f"Attach at most {MAX_FILES_PER_MESSAGE} files to one question")
    if ids:
        found = db.q("SELECT id FROM assistant_files WHERE conversation_id = %s AND id = ANY(%s)",
                     (conversation_id, ids))
        if len(found) != len(ids):
            raise ChatError("A file isn't part of this conversation")
    return ids


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def add_message(conversation_id, role, content, file_ids=None, sources=None, check=None, error=False):
    """Save one message and bump the conversation. The first question names a 'New chat'."""
    file_ids = list(file_ids or [])

    def write(conn):
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO assistant_messages (conversation_id, role, content, sources, check_result, file_ids, error) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id, created_at",
                (conversation_id, role, content or "", json.dumps(sources or []),
                 json.dumps(check) if check is not None else None, json.dumps(file_ids), bool(error)),
            )
            msg_id, created = cur.fetchone()
            if role == "user":
                cur.execute(
                    "UPDATE assistant_conversations SET updated_at = now(), "
                    "title = CASE WHEN title = 'New chat' THEN %s ELSE title END WHERE id = %s",
                    (_clean_title(content or "Files"), conversation_id),
                )
            else:
                cur.execute("UPDATE assistant_conversations SET updated_at = now() WHERE id = %s", (conversation_id,))
            return msg_id, created

    msg_id, created = db.transaction(write)
    files = []
    if file_ids:
        rows = db.q("SELECT id, filename, mime, size_bytes, text_content IS NOT NULL AS has_text "
                    "FROM assistant_files WHERE id = ANY(%s)", (file_ids,))
        by_id = {r["id"]: _file_json(r) for r in rows}
        files = [by_id[f] for f in file_ids if f in by_id]
    return {"id": msg_id, "role": role, "content": content or "", "sources": sources or [],
            "check": check, "files": files, "error": bool(error), "createdAt": _iso(created)}


def _file_blocks(f):
    """API content blocks for one stored file (f has filename, mime, data|None, text_content)."""
    name = f["filename"]
    if f.get("text_content") is not None:
        return [{"type": "document", "title": name,
                 "source": {"type": "text", "media_type": "text/plain", "data": f["text_content"]}}]
    if f.get("data") is None:
        return [{"type": "text", "text": f"[File \"{name}\" was attached earlier but is too large to re-send "
                                          "with the whole conversation. Ask Seif to attach it again if you need it.]"}]
    b64 = base64.b64encode(bytes(f["data"])).decode("ascii")
    if f["mime"] == "application/pdf":
        return [{"type": "document", "title": name,
                 "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}]
    return [{"type": "text", "text": f"[Image: {name}]"},
            {"type": "image", "source": {"type": "base64", "media_type": f["mime"], "data": b64}}]


def _load_files(file_ids, newest_first_ids):
    """Load files for sending, newest first, within the per-request budgets."""
    if not file_ids:
        return {}
    meta = db.q("SELECT id, filename, mime, size_bytes, length(text_content) AS text_len "
                "FROM assistant_files WHERE id = ANY(%s)", (list(file_ids),))
    meta = {m["id"]: m for m in meta}
    send_bytes, send_text, binary_ids, text_ids = 0, 0, [], []
    for fid in newest_first_ids:
        m = meta.get(fid)
        if not m or fid in binary_ids or fid in text_ids:
            continue
        if m["text_len"] is not None:
            if send_text + m["text_len"] <= SEND_TEXT_BUDGET:
                send_text += m["text_len"]
                text_ids.append(fid)
        elif send_bytes + m["size_bytes"] <= SEND_BYTES_BUDGET:
            send_bytes += m["size_bytes"]
            binary_ids.append(fid)
    wanted = binary_ids + text_ids
    rows = db.q("SELECT id, filename, mime, data, text_content FROM assistant_files WHERE id = ANY(%s)",
                (wanted,)) if wanted else []
    loaded = {r["id"]: r for r in rows}
    out = {}
    for fid, m in meta.items():
        out[fid] = loaded.get(fid) or {"filename": m["filename"], "mime": m["mime"], "data": None, "text_content": None}
    return out


def build_turns(conversation_id, new_file_ids, new_text):
    """(history, new turn blocks) for the engine.

    History is the last HISTORY_MESSAGES saved messages (failed answers left
    out), each user turn with its files re-sent in place so the cached prefix
    stays identical from one question to the next.
    """
    rows = db.q(
        "SELECT id, role, content, file_ids FROM assistant_messages "
        "WHERE conversation_id = %s AND NOT error ORDER BY id DESC LIMIT %s",
        (conversation_id, HISTORY_MESSAGES),
    )
    rows.reverse()
    # Only Seif's files are re-sent as files. The assistant's own documents come
    # back as their source text after its reply, so it can change them.
    newest_first = list(new_file_ids)
    for r in reversed(rows):
        if r["role"] == "user":
            newest_first.extend(r.get("file_ids") or [])
    files = _load_files(set(newest_first), newest_first)
    made_ids = [f for r in rows if r["role"] == "assistant" for f in (r.get("file_ids") or [])]
    made = {}
    if made_ids:
        made = {d["id"]: d["text_content"] for d in db.q(
            "SELECT id, text_content FROM assistant_files WHERE id = ANY(%s) AND text_content IS NOT NULL",
            (made_ids,))}

    def blocks(text, ids):
        out = []
        for fid in ids:
            if fid in files:
                out.extend(_file_blocks(files[fid]))
        out.append({"type": "text", "text": text or "(See the attached files.)"})
        return out

    history = []
    for r in rows:
        if r["role"] == "user":
            history.append({"role": "user", "content": blocks(r["content"], r.get("file_ids") or [])})
        else:
            drafted = [made[f] for f in (r.get("file_ids") or []) if f in made]
            text = r["content"].strip()
            if drafted:
                text += "\n\n<drafted_documents>\n" + "\n\n".join(drafted) + "\n</drafted_documents>"
            if text.strip():
                history.append({"role": "assistant", "content": text})
    return history, blocks(new_text, new_file_ids)
