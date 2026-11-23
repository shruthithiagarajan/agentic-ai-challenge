"""Chat history for `desk chat`: slash commands, save/load, and a token-budget sliding window."""
import json
import re
from pathlib import Path

from .llm import estimate_tokens

DEFAULT_MAX_HISTORY_TOKENS = 3000  # safely under Groq's 8K tokens-per-minute cap (free tier)
MESSAGE_OVERHEAD = 4  # rough per-message tokens for role/formatting
SESSIONS_DIR = Path(".desk_sessions")
HELP = "commands: /reset  /save <name>  /load <name>  /exit"
_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}$")


def count_tokens(messages):
    """Estimated prompt size of a message list (chars//4 + overhead per message)."""
    return sum(estimate_tokens(m["content"]) + MESSAGE_OVERHEAD for m in messages)


def safe_name(name):
    """Return name if it is a plain file stem; reject path traversal ('..', '/', '\\') and odd characters."""
    if not name or ".." in name or "/" in name or "\\" in name or not _NAME.match(name):
        raise ValueError(f"invalid session name {name!r}: use letters, digits, '-', '_' (no '..', '/' or '\\')")
    return name


class Session:
    """System prompt (always pinned) + alternating user/assistant turns."""

    def __init__(self, system, max_tokens=DEFAULT_MAX_HISTORY_TOKENS, pin_first=False, directory=SESSIONS_DIR):
        self.system = system
        self.max_tokens = max_tokens
        self.pin_first = pin_first  # Stretch: never drop the first user message
        self.directory = Path(directory)
        self.turns = []

    def messages(self, user=None):
        """Messages to send: system + history (+ the new user message)."""
        msgs = [{"role": "system", "content": self.system}, *self.turns]
        if user is not None:
            msgs.append({"role": "user", "content": user})
        return msgs

    def add_turn(self, user, assistant):
        self.turns += [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]

    def reset(self):
        self.turns.clear()

    def _drop_oldest(self):
        """Drop the oldest user/assistant pair (or a lone reply after a pinned first message)."""
        start = 1 if self.pin_first else 0
        if len(self.turns) <= start:
            return 0
        n = 2 if self.turns[start]["role"] == "user" else 1
        del self.turns[start:start + n]
        return n

    def fit(self, user):
        """Drop oldest history until system + history + user fits max_tokens. Returns messages dropped."""
        dropped = 0
        while count_tokens(self.messages(user)) > self.max_tokens:
            n = self._drop_oldest()
            if not n:
                break  # only pinned messages left: send anyway, the API reports if it is too large
            dropped += n
        return dropped

    def path(self, name):
        return self.directory / f"{safe_name(name)}.json"

    def save(self, name):
        p = self.path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"version": 1, "turns": self.turns}, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    def load(self, name):
        p = self.path(name)
        if not p.is_file():
            raise ValueError(f"no saved session named {name!r}")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise ValueError(f"session {name!r} is not valid JSON") from None
        turns = data.get("turns") if isinstance(data, dict) else None
        ok = isinstance(turns, list) and all(
            isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) for m in turns)
        if not ok:
            raise ValueError(f"session {name!r} has an unexpected format")
        self.turns = [{"role": m["role"], "content": m["content"]} for m in turns]
        return len(self.turns)


def handle_command(session, line):
    """Run a /command. Returns (action, message) where action is 'exit' or 'ok'."""
    cmd, _, arg = line.strip().partition(" ")
    arg = arg.strip()
    try:
        if cmd == "/exit":
            return "exit", "bye"
        if cmd == "/reset":
            session.reset()
            return "ok", "history cleared"
        if cmd in ("/save", "/load") and not arg:
            return "ok", f"usage: {cmd} <name>"
        if cmd == "/save":
            return "ok", f"saved {len(session.turns)} messages to {session.save(arg)}"
        if cmd == "/load":
            return "ok", f"loaded {session.load(arg)} messages from {arg!r}"
    except (ValueError, OSError) as e:
        return "ok", f"error: {e}"
    return "ok", f"unknown command {cmd!r}. {HELP}"
