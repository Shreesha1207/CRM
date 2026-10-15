"""A calendar entry (iCalendar, RFC 5545) for a booking, to attach to e-mails.

Calendar programs match entries by UID. Sending the same UID again with a higher
SEQUENCE (METHOD:REQUEST) replaces the entry, which is how a moved booking
replaces its old time; METHOD:CANCEL removes it.

Shaped like the invitations Google Calendar sends, because that is what Gmail
recognises: the customer is a guest asked to reply (NEEDS-ACTION, RSVP), so Gmail
shows the invitation with Yes / No / Maybe, and answering Yes puts it in their
calendar (Google only adds invitations from unknown senders by itself if the
customer allows it in their Calendar settings).
"""

from datetime import UTC, datetime


def _escape(text: str) -> str:
    text = text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Lines are at most 75 octets; longer ones continue on a line starting with a space."""
    out: list[str] = []
    current, size, limit = "", 0, 75
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > limit:
            out.append(current)
            current, size, limit = " ", 1, 75  # the continuation's leading space counts
        current += char
        size += width
    out.append(current)
    return "\r\n".join(out)


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _name(text: str) -> str:
    return '"' + text.replace('"', "'").replace("\r", " ").replace("\n", " ") + '"'


def build(
    *,
    method: str,
    uid: str,
    sequence: int,
    start: datetime,
    end: datetime,
    stamp: datetime,
    summary: str,
    description: str,
    location: str | None,
    url: str,
    organizer: tuple[str, str],
    attendee: tuple[str, str],
    created: datetime | None = None,
) -> str:
    """One event. `method` is REQUEST (add or update) or CANCEL (remove); (name, e-mail) pairs for the people."""
    cancelled = method == "CANCEL"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Booking platform//EN",
        "CALSCALE:GREGORIAN",
        f"METHOD:{method}",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SEQUENCE:{sequence}",
        f"DTSTAMP:{_utc(stamp)}",
        f"CREATED:{_utc(created or stamp)}",
        f"LAST-MODIFIED:{_utc(stamp)}",
        f"DTSTART:{_utc(start)}",
        f"DTEND:{_utc(end)}",
        f"SUMMARY:{_escape(summary)}",
        *([f"LOCATION:{_escape(location)}"] if location else []),
        f"DESCRIPTION:{_escape(description)}",
        f"URL:{url}",
        f"STATUS:{'CANCELLED' if cancelled else 'CONFIRMED'}",
        "TRANSP:OPAQUE",
        f"ORGANIZER;CN={_name(organizer[0])}:mailto:{organizer[1]}",
        f"ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;CN={_name(attendee[0])};X-NUM-GUESTS=0:mailto:{attendee[1]}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "".join(_fold(line) + "\r\n" for line in lines)
