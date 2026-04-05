"""Generate calendar.ics from result.json."""
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path


def generate_ics(result: dict) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Tokyo Art Calendar//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:Tokyo Art Calendar",
        "X-WR-TIMEZONE:Asia/Tokyo",
    ]
    for src in result["sources"]:
        for ev in src["events"]:
            if not ev.get("start_date"):
                continue
            sd = ev["start_date"].replace("-", "")
            ed = ev.get("end_date", "")
            if ed:
                end_dt = datetime.strptime(ed, "%Y-%m-%d") + timedelta(days=1)
                ed = end_dt.strftime("%Y%m%d")
            else:
                ed = sd
            uid = hashlib.md5(f"{ev.get('title','')}{sd}{src['name']}".encode()).hexdigest()
            summary = (ev.get("title") or "").replace(",", "\\,").replace("\n", " ")
            venue = (ev.get("venue") or "").replace(",", "\\,").replace("\n", " ")
            desc_parts = []
            if ev.get("summary"):
                desc_parts.append(ev["summary"])
            if ev.get("admission"):
                desc_parts.append(ev["admission"])
            if ev.get("closed_days"):
                desc_parts.append(f"休館: {ev['closed_days']}")
            if ev.get("reservation_required"):
                desc_parts.append("要予約")
            desc = "\\n".join(desc_parts).replace(",", "\\,")
            url = ev.get("url") or ""
            lines.append("BEGIN:VEVENT")
            lines.append(f"UID:{uid}@art-calendar-tokyo")
            lines.append(f"DTSTART;VALUE=DATE:{sd}")
            lines.append(f"DTEND;VALUE=DATE:{ed}")
            lines.append(f"SUMMARY:{summary}")
            if venue:
                lines.append(f"LOCATION:{venue}")
            if desc:
                lines.append(f"DESCRIPTION:{desc}")
            if url:
                lines.append(f"URL:{url}")
            lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


if __name__ == "__main__":
    result = json.loads(Path("result.json").read_text(encoding="utf-8"))
    ics = generate_ics(result)
    Path("calendar.ics").write_text(ics, encoding="utf-8")
    event_count = sum(len(s["events"]) for s in result["sources"])
    print(f"Generated calendar.ics: {event_count} events")
