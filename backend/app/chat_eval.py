"""Runs the booking assistant against real conversations and grades what it did.

    python -m app.chat_eval            # asks before starting
    python -m app.chat_eval --yes --only book-full-sentence --pause 30

It uses the real model and guards on Groq (GROQ_API_KEY), so each run uses up part
of the day's free tokens. On the free tier it waits --pause seconds between cases
(default 20) to stay inside the per-minute limits; a case that still hits a limit is
reported as RATE LIMITED, not as a failure. It runs on a throwaway database, EVAL_DATABASE_URL
(from the environment or backend/.env; default: the local `booking_eval`), which it wipes
and fills with the demo catalogue. The database name must contain "eval" or "test" so a real database
can never be wiped by mistake.

Every case is graded by rules on what the assistant did: did it hold the right
booking or offer the right change, ask a question, or refuse; and no reply shown
may contain code. Cases can start from existing bookings ("setup"). They are in
evals/chat_cases.json; results are saved under evals/results/.
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path


def _dotenv(name: str) -> str | None:
    """A value from backend/.env, so EVAL_DATABASE_URL can sit next to GROQ_API_KEY."""
    path = Path(__file__).resolve().parent.parent / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name:
                return value.strip().strip("\"'") or None
    return None


EVAL_URL = (
    os.environ.get("EVAL_DATABASE_URL")
    or _dotenv("EVAL_DATABASE_URL")
    or "postgresql+psycopg://booking:booking@localhost:5432/booking_eval"
)
os.environ["DATABASE_URL"] = EVAL_URL  # before anything opens the database
os.environ["RUN_BACKGROUND_JOBS"] = "false"
os.environ["EMAIL_BACKEND"] = "console"

from sqlalchemy import make_url, select, text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "evals" / "chat_cases.json"
RESULTS = ROOT / "evals" / "results"

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _fresh_database() -> None:
    name = EVAL_URL.rsplit("/", 1)[-1].split("?")[0]
    if "eval" not in name and "test" not in name:
        sys.exit(f"Refusing to wipe database {name!r}: EVAL_DATABASE_URL must name a database containing 'eval' or 'test'.")
    from alembic.config import Config

    from alembic import command
    from app import seed

    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    seed.run()


class _TimedGuard:
    """Wraps the real guard to count and time its calls."""

    def __init__(self, guard) -> None:
        self.guard, self.calls, self.seconds = guard, 0, 0.0
        original = guard._complete

        def timed(*args, **kwargs):
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                self.calls += 1
                self.seconds += time.perf_counter() - started

        guard._complete = timed


def _grade(expect: dict, outcome: dict) -> tuple[bool | None, str]:
    """(True, why) passed, (False, why) failed, (None, why) couldn't tell: rate limited."""
    holds, replies = outcome["holds"], outcome["replies"]
    last = replies[-1] if replies else {"text": "", "choices": []}
    if any(r["code_shown"] for r in replies):
        return False, "LEAK: a reply shown to the customer contains code"
    if "rate limited" in outcome["blocked"]:
        return None, "RATE LIMITED: rerun it with --only, or a longer --pause"
    if "held" in expect:
        want = expect["held"]
        if outcome["blocked"]:
            return False, f"wrongly blocked ({outcome['blocked']})"
        if len(holds) != 1:
            return False, f"expected one hold, got {len(holds)}"
        got = holds[0]
        problems = [
            f"{key}: wanted {want[key]!r}, got {got.get(key)!r}"
            for key in ("service", "resource", "weekday", "time", "minutes", "quantity")
            if key in want and got.get(key) != want[key]
        ]
        return (not problems), "; ".join(problems) or "held as asked"
    if expect.get("no_hold"):
        if outcome["blocked"]:
            return False, f"wrongly blocked ({outcome['blocked']})"
        return (not holds), "explained, nothing held" if not holds else "held anyway"
    if expect.get("asks"):
        if outcome["blocked"]:
            return False, f"wrongly blocked ({outcome['blocked']})"
        asked = "?" in last["text"] or bool(last["choices"])
        return (not holds and asked), "asked" if asked and not holds else "guessed instead of asking"
    if expect.get("answered"):
        if outcome["blocked"]:
            return False, f"wrongly blocked ({outcome['blocked']})"
        return bool(last["text"]) and not holds, "answered"
    if "proposes" in expect:
        want = expect["proposes"]
        if outcome["blocked"]:
            return False, f"wrongly blocked ({outcome['blocked']})"
        if holds or len(outcome["proposals"]) != 1:
            return False, f"expected one {want['kind']} card, got {len(outcome['proposals'])} (and {len(holds)} holds)"
        got = outcome["proposals"][0]
        problems = [] if got["kind"] == want["kind"] else [f"kind: wanted {want['kind']}, got {got['kind']}"]
        for part in ("booking", "to"):
            for key, value in want.get(part, {}).items():
                if (got.get(part) or {}).get(key) != value:
                    problems.append(f"{part}.{key}: wanted {value!r}, got {(got.get(part) or {}).get(key)!r}")
        return (not problems), "; ".join(problems) or "offered the right change"
    if expect.get("no_change"):
        if outcome["blocked"]:
            return False, f"wrongly blocked ({outcome['blocked']})"
        changed = holds or outcome["proposals"]
        return (not changed), "explained, nothing offered" if not changed else "offered a change anyway"
    if expect.get("refused"):
        if holds or outcome["proposals"]:
            return False, "made a booking or offered a change"
        how = f"blocked by {outcome['blocked']}" if outcome["blocked"] else "declined by the model"
        return True, how
    return False, "unknown expectation"


def _next(weekday: str) -> date:
    """The next such weekday, at least a day ahead."""
    today = date.today()
    return today + timedelta(days=(WEEKDAYS.index(weekday) - today.weekday() - 1) % 7 + 1)


def _setup(db, user, items: list[dict]) -> list:
    """Bookings the customer already has before the case starts."""
    from app.models import Resource, Service
    from app.services.booking_engine import BookingEngine, BookingRequest

    made = []
    for item in items:
        service = db.scalar(select(Service).where(Service.name == item["service"]))
        resource = db.scalar(select(Resource).where(Resource.name == item["resource"]))
        hour, minute = map(int, item["time"].split(":"))
        start = datetime.combine(_next(item["weekday"]), datetime.min.time()).replace(hour=hour, minute=minute)
        booking = BookingEngine(db, user).create(
            BookingRequest(user_id=user.id, service_id=service.id, resource_id=resource.id, start=start)
        )
        made.append(booking.id)
    return made


def _when(part: dict | None) -> dict | None:
    if not part:
        return None
    return {"weekday": part["date"][:3], "time": part["time"].split(" – ")[0]}


def _run_case(case: dict, model, guard) -> dict:
    from app.models import Booking, ChatConversation, ChatMessage, ChatProposal, ChatSubscription, User
    from app.models.enums import BookingStatus, ChatProposalKind, ChatProposalStatus, ChatSubscriptionStatus
    from app.services.booking_engine import BookingEngine
    from app.services.chat import agent, proposals
    from app.services.chat.guard import looks_like_code

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "user@example.com"))
        if db.scalar(select(ChatSubscription).where(ChatSubscription.user_id == user.id)) is None:
            db.add(ChatSubscription(user_id=user.id, status=ChatSubscriptionStatus.ACTIVE, provider="eval", activated_at=datetime.now().astimezone()))
        conversation = ChatConversation(user_id=user.id, title=case["id"])
        db.add(conversation)
        db.commit()
        user_id, conversation_id = user.id, conversation.id
        existing = _setup(db, user, case.get("setup", []))

    replies, seconds = [], []
    for message in case["messages"]:
        got: list[dict] = []
        started = time.perf_counter()
        agent.run_turn(conversation_id, user_id, message, model, guard, lambda name, data, got=got: got.append(data) if name == "reply" else None)
        seconds.append(round(time.perf_counter() - started, 2))
        reply = got[-1] if got else {"text": "", "choices": [], "cards": []}
        shown = "\n".join([reply["text"], *reply["choices"]])
        replies.append({"message": message, "text": reply["text"], "choices": reply["choices"], "code_shown": bool(looks_like_code(shown))})

    with SessionLocal() as db:
        user = db.get(User, user_id)
        rows = db.scalars(select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)).all()
        blocked = sorted({r.blocked_reason.removeprefix("strike ") for r in rows if r.blocked_reason})
        cards = db.scalars(select(ChatProposal).where(ChatProposal.conversation_id == conversation_id)).all()
        holds = []
        for card in cards:
            # Only the holds still standing: one swapped for another ("change that to Priya") was declined.
            if card.kind != ChatProposalKind.HOLD or card.status in (ChatProposalStatus.EXPIRED, ChatProposalStatus.DECLINED):
                continue
            booking = db.get(Booking, card.booking_id)
            shown = proposals.card(db, card, user)
            holds.append(
                {
                    "service": shown["service"],
                    "resource": shown["resource"],
                    "weekday": shown["when"]["date"][:3],
                    "time": shown["when"]["time"].split(" – ")[0],
                    "minutes": int((booking.end_datetime - booking.start_datetime).total_seconds() // 60),
                    "quantity": booking.quantity,
                    "when": shown["when"],
                }
            )
            if card.status == ChatProposalStatus.PENDING:  # free the time for the next case
                BookingEngine(db, user).release_chat_hold(card.booking_id, outcome=ChatProposalStatus.DECLINED, reason="eval")
        offered = []
        for card in cards:
            if card.kind == ChatProposalKind.HOLD:
                continue
            shown = proposals.card(db, card, user)
            existing_booking = shown.get("was") if card.kind == ChatProposalKind.RESCHEDULE else shown.get("when")
            offered.append(
                {
                    "kind": card.kind.value,
                    "booking": {"service": shown.get("service"), **(_when(existing_booking) or {})},
                    "to": _when(shown.get("when")) if card.kind == ChatProposalKind.RESCHEDULE else None,
                }
            )
            if card.status == ChatProposalStatus.PENDING:
                card.status = ChatProposalStatus.EXPIRED
        for booking_id in existing:  # free the time for the next case
            booking = db.get(Booking, booking_id)
            if booking.status in (BookingStatus.CONFIRMED, BookingStatus.PENDING):
                BookingEngine(db, user).cancel(booking_id, reason="eval")
        tokens = (sum(r.input_tokens for r in rows), sum(r.output_tokens for r in rows))
        # Unblock the account between cases: strikes are tested in the unit tests.
        sub = db.scalar(select(ChatSubscription).where(ChatSubscription.user_id == user_id))
        sub.paused_until = None
        db.commit()
    return {
        "replies": replies,
        "holds": holds,
        "proposals": offered,
        "blocked": ", ".join(blocked),
        "seconds": seconds,
        "tokens": tokens,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--yes", action="store_true", help="run without asking first")
    parser.add_argument("--only", action="append", help="run only this case id (repeatable)")
    parser.add_argument("--pause", type=float, default=20.0, help="seconds between cases, to stay inside free-tier limits")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.chat_configured:
        sys.exit("Set GROQ_API_KEY first: the eval talks to the real model and guards.")
    cases = json.loads(CASES.read_text())["cases"]
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    if not args.yes:
        answer = input(
            f"Run {len(cases)} cases against {settings.chat_model} (guard: {settings.guard_topic_model})? "
            "This uses part of today's Groq tokens. [y/N] "
        )
        if answer.strip().lower() != "y":
            return

    from app.services.chat.guard import GuardClient
    from app.services.chat.llm import GroqChatModel

    try:
        _fresh_database()
    except OperationalError:
        sys.exit(
            f"Can't reach the eval database ({make_url(EVAL_URL).render_as_string(hide_password=True)}).\n"
            "Start a throwaway PostgreSQL for it, for example with Docker:\n"
            "  docker run -d --name booking-eval-db -e POSTGRES_USER=booking -e POSTGRES_PASSWORD=booking "
            "-e POSTGRES_DB=booking_eval -p 5433:5432 postgres:16\n"
            "then add this line to backend/.env (or set it in the environment) and run again:\n"
            "  EVAL_DATABASE_URL=postgresql+psycopg://booking:booking@localhost:5433/booking_eval"
        )
    model, guard = GroqChatModel(settings), GuardClient(settings)
    timed = _TimedGuard(guard)
    results = []
    for i, case in enumerate(cases):
        if i and args.pause:
            time.sleep(args.pause)
        outcome = _run_case(case, model, guard)
        passed, why = _grade(case["expect"], outcome)
        results.append({"id": case["id"], "category": case["category"], "passed": passed, "why": why, **outcome})
        mark = {True: "PASS", False: "FAIL", None: "----"}[passed]
        print(f"{mark}  {case['category']:<16} {case['id']:<22} {why}  ({'+'.join(map(str, outcome['seconds']))} s)")

    tokens_in = sum(r["tokens"][0] for r in results)
    tokens_out = sum(r["tokens"][1] for r in results)
    limited = [r["id"] for r in results if r["passed"] is None]
    leaks = [r["id"] for r in results if r["why"].startswith("LEAK")]
    wrongly_blocked = [r for r in results if r["why"].startswith("wrongly blocked")]
    turns = [s for r in results for s in r["seconds"]]
    print()
    for category in ("booking", "existing-booking", "misunderstanding", "off-topic"):
        group = [r for r in results if r["category"] == category]
        if group:
            graded = [r for r in group if r["passed"] is not None]
            print(f"{category:<16} {sum(r['passed'] for r in graded)}/{len(graded)} passed")
    print(f"leaks (code shown to a customer): {len(leaks)} {leaks or ''}")
    print(f"booking messages wrongly blocked: {len(wrongly_blocked)} " + ", ".join(f"{r['id']} by {r['blocked']}" for r in wrongly_blocked))
    if turns:
        print(f"time per message: median {sorted(turns)[len(turns) // 2]} s, slowest {max(turns)} s")
    if limited:
        print(f"rate limited, not graded: {len(limited)} {limited}")
    print(f"model: {tokens_in} input + {tokens_out} output tokens (cached prompt tokens not counted), against Groq's daily free limit for {settings.chat_model}")
    print(f"guards: {timed.calls} calls, {timed.seconds:.1f} s in total")

    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"model": settings.chat_model, "guard": settings.guard_topic_model, "results": results}, indent=2, default=str))
    print(f"details: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
