"""FRIDAY core logic (no Kivy imports, so it can be unit-tested on a desktop).

Contains: settings storage, SQLite chat store, markdown -> Kivy markup,
request building, and a streaming OpenAI Responses API client.
"""
import base64
import json
import mimetypes
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

API_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-4.1-mini"
MODEL_PRESETS = ["gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini", "gpt-4o"]
BASE_INSTRUCTIONS = (
    "You are Friday, a helpful, clear, friendly personal AI assistant running on "
    "a phone. Keep answers concise and easy to read on a small screen unless the "
    "user asks for depth. For images, describe what is visible and answer the "
    "user's question. Be honest about uncertainty."
)

DEFAULT_SETTINGS = {
    "theme": "Obsidian",
    "model": DEFAULT_MODEL,
    "api_key": "",
    "speak_replies": False,
    "web_search": False,
    "custom_instructions": "",
    "voice_lang": "",
    "active_chat": None,
}


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
class Settings:
    def __init__(self, folder):
        self.path = os.path.join(folder, "settings.json")
        self.data = dict(DEFAULT_SETTINGS)
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except Exception:
            pass

    def get(self, key):
        return self.data.get(key, DEFAULT_SETTINGS.get(key))

    def set(self, **kw):
        self.data.update(kw)
        self.save()

    def save(self):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f)
            try:
                os.chmod(tmp, 0o600)
            except Exception:
                pass
            os.replace(tmp, self.path)  # atomic: never leaves a half-written file
        except Exception:
            pass


# --------------------------------------------------------------------------
# Chat store
# --------------------------------------------------------------------------
class Store:
    def __init__(self, path):
        self.path = path
        with self._con() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS chats(
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT,
                created_at TEXT, updated_at TEXT)""")
            con.execute("""CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER,
                role TEXT, content TEXT, image_path TEXT, created_at TEXT)""")
            con.execute("CREATE INDEX IF NOT EXISTS idx_msg_chat ON messages(chat_id, id)")

    def _con(self):
        con = sqlite3.connect(self.path, timeout=10)
        return _Conn(con)

    def new_chat(self, title="New chat"):
        t = now_iso()
        with self._con() as con:
            return con.execute(
                "INSERT INTO chats(title,created_at,updated_at) VALUES(?,?,?)",
                (title, t, t)).lastrowid

    def chat_title(self, chat_id):
        with self._con() as con:
            row = con.execute("SELECT title FROM chats WHERE id=?", (chat_id,)).fetchone()
        return row[0] if row else None

    def set_title(self, chat_id, title):
        with self._con() as con:
            con.execute("UPDATE chats SET title=? WHERE id=?", (title, chat_id))

    def list_chats(self, query=""):
        with self._con() as con:
            if query:
                like = "%" + query.replace("%", "").replace("_", "") + "%"
                return con.execute(
                    """SELECT DISTINCT c.id,c.title,c.updated_at FROM chats c
                       LEFT JOIN messages m ON m.chat_id=c.id
                       WHERE c.title LIKE ? OR m.content LIKE ?
                       ORDER BY c.updated_at DESC LIMIT 100""", (like, like)).fetchall()
            return con.execute(
                "SELECT id,title,updated_at FROM chats ORDER BY updated_at DESC LIMIT 200"
            ).fetchall()

    def messages(self, chat_id):
        with self._con() as con:
            rows = con.execute(
                "SELECT id,role,content,image_path FROM messages WHERE chat_id=? ORDER BY id",
                (chat_id,)).fetchall()
        return [{"id": r[0], "role": r[1], "content": r[2] or "", "image_path": r[3]}
                for r in rows]

    def add_message(self, chat_id, role, content, image_path=None):
        with self._con() as con:
            mid = con.execute(
                "INSERT INTO messages(chat_id,role,content,image_path,created_at) VALUES(?,?,?,?,?)",
                (chat_id, role, content, image_path, now_iso())).lastrowid
            con.execute("UPDATE chats SET updated_at=? WHERE id=?", (now_iso(), chat_id))
        return mid

    def delete_message(self, message_id):
        with self._con() as con:
            con.execute("DELETE FROM messages WHERE id=?", (message_id,))

    def delete_chat(self, chat_id):
        """Delete a chat and any attachment files that only this chat uses."""
        with self._con() as con:
            paths = [r[0] for r in con.execute(
                "SELECT image_path FROM messages WHERE chat_id=? AND image_path IS NOT NULL",
                (chat_id,)).fetchall()]
            con.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
            con.execute("DELETE FROM chats WHERE id=?", (chat_id,))
        for p in paths:
            try:
                os.remove(p)
            except Exception:
                pass


class _Conn:
    """sqlite3 connection that commits AND closes (the stdlib one only commits)."""
    def __init__(self, con):
        self.con = con

    def __enter__(self):
        return self.con

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.con.commit()
            else:
                self.con.rollback()
        finally:
            self.con.close()
        return False


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------
def escape_markup(text):
    return text.replace("&", "&amp;").replace("[", "&bl;").replace("]", "&br;")


def md_to_markup(text, mono_font=None, code_color="aaaaaa"):
    """Convert a small, safe subset of Markdown into Kivy markup."""
    font_open = "[font=%s]" % mono_font if mono_font else ""
    font_close = "[/font]" if mono_font else ""
    out = []
    for part in re.split(r"(```.*?```|```.*$)", text, flags=re.S):
        if part.startswith("```"):
            body = part[3:]
            if body.endswith("```"):
                body = body[:-3]
            first, _, rest = body.partition("\n")
            if _ and re.fullmatch(r"[\w+#.-]*\s*", first):
                body = rest  # drop the language tag line
            out.append("[color=%s]%s%s%s[/color]" % (
                code_color, font_open, escape_markup(body.strip("\n")), font_close))
            continue
        t = escape_markup(part)
        t = re.sub(r"(?m)^(\s*)#{1,6}\s+(.*)$", r"\1[b]\2[/b]", t)
        t = re.sub(r"(?m)^(\s*)[-*+]\s+", "\\1\u2022 ", t)
        t = re.sub(r"\*\*(.+?)\*\*", r"[b]\1[/b]", t)
        t = re.sub(r"`([^`\n]+)`", lambda m: "[color=%s]%s%s%s[/color]" % (
            code_color, font_open, m.group(1), font_close), t)
        out.append(t)
    return "".join(out)


def strip_markdown(text):
    """Plain text for text-to-speech."""
    t = re.sub(r"```.*?```", " code block. ", text, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"(?m)^\s*#{1,6}\s*", "", t)
    t = re.sub(r"(?m)^\s*[-*+]\s+", "", t)
    t = t.replace("**", "").replace("__", "")
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    return t.strip()


def make_title(text, fallback="Image chat", limit=38):
    t = " ".join((text or "").split())
    if not t:
        return fallback
    return t if len(t) <= limit else t[:limit - 1].rstrip() + "\u2026"


def chat_as_text(messages):
    lines = []
    for m in messages:
        who = "You" if m["role"] == "user" else "Friday"
        lines.append("%s: %s" % (who, m["content"]))
    return "\n\n".join(lines)


# --------------------------------------------------------------------------
# Request building
# --------------------------------------------------------------------------
def encode_image(path):
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return "data:%s;base64,%s" % (mime, b64)


def build_input(messages, max_images=2, max_chars=48000, max_messages=40):
    """Turn stored messages into Responses API input items.

    * Assistant turns are plain strings (the API rejects `input_text` for the
      assistant role, which broke multi-turn chats).
    * Only the newest `max_images` images are re-uploaded, to save data/cost.
    * History is trimmed to a character budget, oldest first.
    """
    picked, budget = [], max_chars
    for m in reversed(messages[-max_messages:]):
        cost = len(m["content"]) + 50
        if picked and budget - cost < 0:
            break
        budget -= cost
        picked.append(m)
    picked.reverse()
    while picked and picked[0]["role"] != "user":
        picked.pop(0)  # always start on a user turn

    image_ids = [id(m) for m in picked if m["role"] == "user" and m.get("image_path")
                 and os.path.isfile(m["image_path"])][-max_images:]
    items = []
    for m in picked:
        if m["role"] == "user" and id(m) in image_ids:
            items.append({"role": "user", "content": [
                {"type": "input_text", "text": m["content"] or "What is in this image?"},
                {"type": "input_image", "image_url": encode_image(m["image_path"])}]})
        else:
            items.append({"role": m["role"], "content": m["content"]})
    return items


def build_instructions(custom=""):
    text = BASE_INSTRUCTIONS + " Today's date is %s." % datetime.now().strftime("%A, %d %B %Y")
    if custom.strip():
        text += "\nUser's custom instructions:\n" + custom.strip()[:2000]
    return text


# --------------------------------------------------------------------------
# API client
# --------------------------------------------------------------------------
class ApiError(Exception):
    pass


def friendly_error(status, body):
    msg = ""
    try:
        msg = json.loads(body).get("error", {}).get("message", "")
    except Exception:
        msg = (body or "")[:200]
    hints = {
        400: "The request was rejected. Check the model name or the attached image.",
        401: "Invalid API key. Open Settings and paste a valid key.",
        403: "This key can't use that model or region.",
        404: "Model not found. Pick a model your key can access.",
        429: "Rate limit or billing problem. Check your OpenAI usage and credits.",
    }
    hint = hints.get(status, "Server problem (%s). Try again shortly." % status
                     if status >= 500 else "Request failed (%s)." % status)
    return "%s\n\n%s" % (hint, msg) if msg else hint


def stream_response(api_key, model, instructions, items, on_delta,
                    cancel=None, web_search=False):
    """Stream a reply. Calls on_delta(str) as text arrives; returns full text."""
    if requests is None:
        raise ApiError("The 'requests' library is missing from this build.")
    payload = {"model": model, "instructions": instructions, "input": items,
               "stream": True, "store": False}
    if web_search:
        payload["tools"] = [{"type": "web_search"}]
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json",
               "Accept": "text/event-stream"}

    resp = None
    for attempt in range(3):
        try:
            resp = requests.post(API_URL, headers=headers, json=payload,
                                 stream=True, timeout=(10, 90))
        except requests.exceptions.Timeout:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise ApiError("The request timed out. Check your connection and try again.")
        except requests.exceptions.ConnectionError:
            raise ApiError("No internet connection.")
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            try:
                wait = min(float(resp.headers.get("Retry-After", "")), 8)
            except ValueError:
                wait = 1.5 * (attempt + 1)
            resp.close()
            time.sleep(wait)
            continue
        break

    if not resp.ok:
        body = resp.text
        resp.close()
        raise ApiError(friendly_error(resp.status_code, body))

    full = []
    try:
        for raw in resp.iter_lines(chunk_size=None):
            if cancel is not None and cancel.is_set():
                break
            if not raw:
                continue
            line = raw.decode("utf-8", "replace")
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                evt = json.loads(data)
            except ValueError:
                continue
            kind = evt.get("type", "")
            if kind == "response.output_text.delta":
                delta = evt.get("delta", "")
                full.append(delta)
                on_delta(delta)
            elif kind in ("response.failed", "error"):
                err = evt.get("error") or evt.get("response", {}).get("error") or {}
                raise ApiError(err.get("message") or "The model returned an error.")
            elif kind == "response.completed":
                break
    except requests.exceptions.RequestException as e:
        if not full:
            raise ApiError("Connection lost while waiting for a reply: %s" % e)
    finally:
        resp.close()
    return "".join(full).strip()
