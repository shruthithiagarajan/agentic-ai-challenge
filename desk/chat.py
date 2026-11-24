"""`desk chat`: a streaming, multi-turn REPL over a Session."""
import sys

from .llm import LLMError, make_client, stream_chat
from .session import HELP, handle_command


def run_chat(session, model, footer, temperature=None, simulate=None, client=None, sleep=None,
             input_fn=input, out=None, err=None):
    """Read lines until /exit or EOF. Slash commands go to the session; anything else is a turn."""
    out, err = out or sys.stdout, err or sys.stderr

    def write(text):
        out.write(text)
        out.flush()

    if client is None and not simulate:
        try:
            client = make_client(model=model)  # one client for the whole chat
        except LLMError as e:
            print(f"error: {e}", file=err)
            return 1
    extra = {"sleep": sleep} if sleep else {}
    print(f"Desk chat ({model}). {HELP}", file=out)
    while True:
        try:
            line = input_fn("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(file=out)
            return 0
        if not line:
            continue
        if line.startswith("/"):
            action, msg = handle_command(session, line)
            print(msg, file=out)
            if action == "exit":
                return 0
            continue
        dropped = session.fit(line)
        if dropped:
            print(f"[history] dropped {dropped} oldest message(s) to stay under {session.max_tokens} tokens", file=err)
        write("desk> ")
        try:
            r = stream_chat(session.messages(line), model, temperature=temperature, simulate=simulate, client=client,
                            log=lambda m: print(f"[retry] {m}", file=err), on_token=write, **extra)
        except LLMError as e:
            print(f"\nerror: {e}", file=err)  # turn not kept: history stays consistent
            continue
        except KeyboardInterrupt:
            print("\n[interrupted - reply not kept]", file=err)
            continue
        session.add_turn(line, r.text)
        print(file=out)
        print(footer(r), file=out)
