"""Deterministic, offline intent recognition.

RAY must stay useful with no API keys and no internet, so the common commands are
recognised locally with plain Python: opening apps and folders, finding files,
creating folders, reminders, tasks, notes, system checks, screenshots, volume,
power actions, playing music, backups and web search.

Anything this cannot place (genuine reasoning, questions, research) is handed to
the AI planner instead.  The parser never guesses silently: every result carries
a confidence and the raw evidence it matched on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

from ..productivity.common import to_iso


@dataclass
class Intent:
    kind: str
    args: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    evidence: str = ""
    speak: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "args": self.args,
            "confidence": round(self.confidence, 2),
            "evidence": self.evidence,
        }


FOLDER_ALIASES = {
    "documents": "Documents", "document": "Documents", "docs": "Documents",
    "downloads": "Downloads", "download": "Downloads",
    "desktop": "Desktop", "pictures": "Pictures", "photos": "Pictures",
    "music": "Music", "videos": "Videos", "movies": "Videos",
    "home": "", "projects": "Projects",
}

WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _clean(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^(hey\s+ray|hi\s+ray|ok\s+ray|ray|hey|ok)[\s,]+", "", text, flags=re.IGNORECASE)
    return text.strip()


def parse_when(text: str, now: datetime | None = None) -> datetime | None:
    """Parse natural time expressions: 'at 6pm', 'tomorrow 9:30', 'in 20 minutes'."""
    now = now or datetime.now()
    lowered = (text or "").lower()

    relative = re.search(r"in\s+(\d+)\s*(second|sec|minute|min|hour|hr|day|week)s?", lowered)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        if unit.startswith("sec"):
            return now + timedelta(seconds=amount)
        if unit.startswith("min"):
            return now + timedelta(minutes=amount)
        if unit.startswith("h"):
            return now + timedelta(hours=amount)
        if unit.startswith("day"):
            return now + timedelta(days=amount)
        if unit.startswith("week"):
            return now + timedelta(weeks=amount)

    if re.search(r"\b(tonight|this evening)\b", lowered):
        return now.replace(hour=20, minute=0, second=0, microsecond=0) if now.hour < 20 else now + timedelta(hours=1)
    if re.search(r"\bthis afternoon\b", lowered):
        return now.replace(hour=15, minute=0, second=0, microsecond=0)
    if re.search(r"\bthis morning\b", lowered):
        return now.replace(hour=9, minute=0, second=0, microsecond=0)
    if re.search(r"\bat noon\b", lowered):
        base = now + timedelta(days=1 if now.hour >= 12 else 0)
        return base.replace(hour=12, minute=0, second=0, microsecond=0)

    target_day: datetime | None = None
    if re.search(r"\btomorrow\b", lowered):
        target_day = now + timedelta(days=1)
    else:
        for index, name in enumerate(WEEKDAY_NAMES):
            if re.search(rf"\b(?:next\s+)?{name}\b", lowered):
                days_ahead = (index - now.weekday()) % 7
                days_ahead = days_ahead or 7
                target_day = now + timedelta(days=days_ahead)
                break

    clock = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", lowered)
    moment: datetime | None = None
    if clock:
        hour = int(clock.group(1))
        minute = int(clock.group(2) or 0)
        meridiem = clock.group(3)
        if meridiem:
            meridiem = meridiem.lower()
            if meridiem == "pm" and hour < 12:
                hour += 12
            if meridiem == "am" and hour == 12:
                hour = 0
        elif hour <= 7 and not target_day:
            # "at 6" almost always means the evening
            hour += 12
        if 0 <= hour < 24 and 0 <= minute < 60:
            base = target_day or now
            moment = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if moment < now and not target_day:
                moment += timedelta(days=1)

    if moment is None and target_day is not None:
        moment = target_day.replace(hour=9, minute=0, second=0, microsecond=0)
    return moment


class IntentParser:
    """Rule-based understanding for the commands people actually type."""

    def parse(self, text: str) -> Intent | None:
        raw = (text or "").strip()
        if not raw:
            return None
        command = _clean(raw)
        lowered = command.lower()

        for handler in (
            self._power_action,
            self._volume,
            self._brightness,
            self._screenshot,
            self._system_check,
            self._reminder,
            self._schedule_event,
            self._task,
            self._note,
            self._memory,
            self._organise,
            self._backup,
            self._delete,
            self._file_search,
            self._create_folder,
            self._web_search,
            self._open_folder_or_app,
            self._media,
            self._list_state,
        ):
            intent = handler(command, lowered)
            if intent:
                return intent
        return None

    # --------------------------------------------------------------- handlers
    def _power_action(self, command: str, lowered: str) -> Intent | None:
        patterns = [
            (r"\b(lock|secure)\s+(the\s+)?(computer|pc|laptop|screen|system)\b", "lock", "Locking the computer."),
            (r"\b(lock)\s+(it|now)\b", "lock", "Locking the computer."),
            (r"\bsleep\b|\bhibernate\b", "sleep", "Putting the computer to sleep."),
            (r"\b(restart|reboot)\b", "restart", "Restarting. This needs your explicit approval."),
            (r"\b(shut\s*down|shutdown|power\s*off|turn\s+off\s+the\s+(pc|computer|laptop))\b", "shutdown",
             "Shutting down. This needs your explicit approval."),
        ]
        for pattern, action, speak in patterns:
            if re.search(pattern, lowered):
                return Intent(f"power_{action}", {"action": action}, 0.95, pattern, speak)
        return None

    def _volume(self, command: str, lowered: str) -> Intent | None:
        match = re.search(r"\b(volume|sound|audio)\b.*?(up|down|mute|max|minimum|(\d{1,3})\s*%)", lowered)
        if not match:
            if re.search(r"\bmute\b", lowered):
                return Intent("volume", {"direction": "mute"}, 0.9, "mute", "Muting the sound.")
            if re.search(r"\b(unmute|turn the sound back on|turn on the sound)\b", lowered):
                return Intent("volume", {"direction": "mute"}, 0.9, "unmute", "Turning the sound back on.")
            return None
        target = match.group(2)
        if target == "up" or target == "max":
            steps = 5 if target == "max" else 2
            return Intent("volume", {"direction": "up", "steps": steps}, 0.9, match.group(0), "Turning the volume up.")
        if target == "down" or target == "minimum":
            steps = 10 if target == "minimum" else 2
            return Intent("volume", {"direction": "down", "steps": steps}, 0.9, match.group(0), "Turning the volume down.")
        if target == "mute":
            return Intent("volume", {"direction": "mute"}, 0.9, match.group(0), "Muting the sound.")
        if match.group(3):
            return Intent(
                "set_volume", {"percent": int(match.group(3))}, 0.85, match.group(0),
                f"Setting the volume to {match.group(3)} percent.",
            )
        return None

    def _brightness(self, command: str, lowered: str) -> Intent | None:
        match = re.search(r"\b(brightness|screen)\b.*?(\d{2,3})\s*%?", lowered)
        if match and re.search(r"brightness", lowered):
            return Intent(
                "set_brightness", {"percent": int(match.group(2))}, 0.85, match.group(0),
                f"Setting brightness to {match.group(2)} percent.",
            )
        return None

    def _screenshot(self, command: str, lowered: str) -> Intent | None:
        if re.search(r"\b(screenshot|screen shot|capture (the )?screen|grab the screen)\b", lowered):
            return Intent(
                "screenshot", {}, 0.95, "screenshot", "Taking a screenshot."
            )
        return None

    def _system_check(self, command: str, lowered: str) -> Intent | None:
        if re.search(
            r"\b(check|how(?:'s| is))\b.*\b(performance|system|computer|laptop|pc|memory|cpu|ram|battery|disk)\b",
            lowered,
        ) or re.search(r"\b(system report|performance report|health check)\b", lowered):
            return Intent(
                "system_report", {}, 0.9, "system report",
                "Checking your system now.",
            )
        return None

    def _reminder(self, command: str, lowered: str) -> Intent | None:
        match = re.search(r"\bremind me (?:to|about|that)?\s*(.+)$", command, re.IGNORECASE)
        if not match:
            return None
        remainder = match.group(1).strip()
        when = parse_when(remainder)
        title = remainder
        if when:
            title = re.split(r"\b(at|in|tomorrow|tonight|on)\b", remainder, maxsplit=1,
                             flags=re.IGNORECASE)[0].strip(" ,.")
        title = title or remainder
        if not title:
            return None
        due = when or (datetime.now() + timedelta(minutes=30))
        return Intent(
            "create_reminder",
            {"title": title[:200], "due_at": to_iso(due)},
            0.9,
            match.group(0)[:80],
            f"Reminder set for {due.strftime('%d %b at %H:%M')}.",
        )

    def _schedule_event(self, command: str, lowered: str) -> Intent | None:
        match = re.search(r"\b(schedule|add to (my )?calendar|book)\s+(.+)$", command, re.IGNORECASE)
        if not match:
            match = re.search(r"\b(create|add)\s+(an?\s+)?(event|meeting|appointment)\s+(.+)$",
                              command, re.IGNORECASE)
            if not match:
                return None
            remainder = match.group(4)
        else:
            remainder = match.group(3)
        when = parse_when(remainder)
        title = re.split(r"\b(at|on|tomorrow|tonight|from)\b", remainder, maxsplit=1,
                         flags=re.IGNORECASE)[0].strip(" ,.")
        title = title or remainder
        start = when or (datetime.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
        return Intent(
            "create_event",
            {"title": title[:200], "start_at": to_iso(start), "end_at": to_iso(start + timedelta(hours=1))},
            0.85,
            match.group(0)[:80],
            f"Scheduled '{title}' for {start.strftime('%d %b at %H:%M')}.",
        )

    def _task(self, command: str, lowered: str) -> Intent | None:
        match = re.search(r"\b(?:add (?:a )?task|new task|todo|to-do|create (?:a )?task)\s*[:\-]?\s*(.+)$",
                          command, re.IGNORECASE)
        if not match:
            return None
        remainder = match.group(1).strip()
        if len(remainder) < 2:
            return None
        priority = "high" if re.search(r"\b(urgent|important|asap)\b", lowered) else "normal"
        when = parse_when(remainder)
        title = re.split(r"\b(at|by|tomorrow|in)\b", remainder, maxsplit=1,
                         flags=re.IGNORECASE)[0].strip(" ,.")
        return Intent(
            "create_task",
            {"title": (title or remainder)[:300], "priority": priority, "due_at": to_iso(when) if when else None},
            0.9,
            match.group(0)[:80],
            f"Task added: {title or remainder}.",
        )

    def _note(self, command: str, lowered: str) -> Intent | None:
        # The verb is required: "find my meeting notes" must stay a file search.
        match = re.search(
            r"\b(?:make|write|take|create|add|jot\s+down|note\s+down)\s+(?:a\s+|an\s+)?note\b"
            r"\s*(?:that|about|:|-)?\s*(.+)$",
            command, re.IGNORECASE,
        )
        if not match:
            match = re.match(r"\s*notes?\s*[:\-]\s*(.+)$", command, re.IGNORECASE)
        if not match:
            match = re.match(r"\s*note\s+that\s+(.+)$", command, re.IGNORECASE)
        if not match or not match.group(1).strip():
            return None
        content = match.group(1).strip().lstrip(":-, ").strip()
        return Intent(
            "create_note", {"body": content[:4000]}, 0.85, match.group(0)[:80], "Note saved."
        )

    def _memory(self, command: str, lowered: str) -> Intent | None:
        match = re.search(r"\b(remember|keep in mind|don'?t forget)\b\s*(that|this|:|-)?\s*(.+)$",
                          command, re.IGNORECASE)
        if not match or "remind me" in lowered:
            return None
        content = match.group(3).strip()
        if len(content) < 4:
            return None
        category = "preference" if re.search(r"\b(i (like|prefer|love|hate)|my favourite|my favorite)\b", lowered) else "fact"
        return Intent(
            "remember",
            {"title": content[:120], "content": content, "category": category},
            0.85,
            match.group(0)[:80],
            "I'll remember that.",
        )

    def _organise(self, command: str, lowered: str) -> Intent | None:
        if re.search(r"\borgani[sz]e\b.*\b(downloads?|folder|files)\b", lowered):
            return Intent(
                "plan_organisation", {}, 0.9, "organise downloads",
                "Let me work out a tidy-up plan for your Downloads folder.",
            )
        if re.search(r"\b(duplicate|same) files\b", lowered):
            return Intent("find_duplicates", {}, 0.85, "duplicates", "Looking for duplicate files.")
        if re.search(r"\b(large|big) files\b", lowered):
            return Intent("find_large_files", {}, 0.85, "large files", "Looking for your largest files.")
        return None

    def _backup(self, command: str, lowered: str) -> Intent | None:
        match = re.search(r"\b(back\s?up|backup)\b\s*(?:of\s+|my\s+|the\s+)?(.+)$",
                          command, re.IGNORECASE)
        if not match:
            return None
        target = match.group(2).strip(" .")
        if not target or target in {"it", "now", "this", "everything"}:
            return Intent("backup_prompt", {}, 0.6, match.group(0), "Which folder should I back up?")
        return Intent(
            "backup", {"target": target}, 0.8, match.group(0)[:80],
            f"Creating a verified backup of {target}.",
        )

    def _delete(self, command: str, lowered: str) -> Intent | None:
        """Deleting a path always goes through the approval gate."""
        match = re.match(
            r"^\s*(?:delete|remove|trash)\s+(?:the\s+|a\s+)?(?:file|folder|directory)\s+(.+)$",
            command, re.IGNORECASE,
        )
        if not match:
            # a bare path ("delete /home/me/x.txt" or "delete C:\\temp\\x.txt")
            match = re.match(
                r"^\s*(?:delete|remove)\s+((?:[~.]{1,2}[\\/]|/|[A-Za-z]:\\).+)$",
                command, re.IGNORECASE,
            )
        if not match:
            return None
        target = match.group(1).strip().strip("\"'")
        if not target or len(target) < 2:
            return None
        return Intent(
            "delete_path", {"path": target}, 0.9, match.group(0)[:80],
            "Deleting that needs your approval first.",
        )

    def _file_search(self, command: str, lowered: str) -> Intent | None:
        patterns = [
            r"\b(?:find|locate|where is|where'?s|look for)\s+(?:my\s+|the\s+|a\s+)?(.+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, command, re.IGNORECASE)
            if not match:
                continue
            query = match.group(1).strip(" ?.")
            if not query or re.search(r"\b(web|internet|online|google)\b", query):
                continue
            extension = None
            kind = None
            ext_match = re.search(
                r"\.(\w{2,5})\b|(\bpdfs?\b|\bdocx?\b|\bdocs\b|\bxlsx?\b|\btxt\b|\bmp3\b|\bmp4\b|\bpng\b|\bjpe?g\b)",
                query, re.IGNORECASE,
            )
            if ext_match:
                found = (ext_match.group(1) or ext_match.group(2) or "").lower()
                extension = {"doc": "docx", "docs": "docx", "jpg": "jpg", "jpeg": "jpg",
                             "pdfs": "pdf", "xls": "xlsx"}.get(found, found)
                query = re.sub(
                    r"\.\w{2,5}\b|\b(pdfs?|docx?|docs|xlsx?|txt|mp3|mp4|png|jpe?g)\b",
                    "", query, flags=re.IGNORECASE,
                ).strip()
            if not query:
                # a bare type word ("find pdfs") becomes a type filter, not a search term
                kind_match = re.search(
                    r"\b(images?|photos?|pictures?|videos?|movies?|music|songs?|audio|"
                    r"spreadsheets?|presentations?|docs?|documents?)\b",
                    query or command, re.IGNORECASE,
                )
                if kind_match:
                    word = kind_match.group(1).lower().rstrip("s")
                    mapping = {"image": "image", "photo": "image", "picture": "image",
                               "video": "video", "movie": "video", "music": "audio",
                               "song": "audio", "audio": "audio", "spreadsheet": "spreadsheet",
                               "presentation": "presentation", "doc": "document",
                               "document": "document"}
                    kind = mapping.get(word)
            query = re.sub(r"^\s*(?:my|the|a|an)\s+", "", query, flags=re.IGNORECASE).strip()
            if re.search(r"\b(latest|newest|most recent|last)\b", query, re.IGNORECASE):
                query = re.sub(r"\b(latest|newest|most recent|last)\b", "", query,
                               flags=re.IGNORECASE).strip()
            root = None
            folder_hit = re.search(
                r"\bin\s+(?:my\s+|the\s+)?(downloads?|documents?|desktop|pictures?|music|videos?)\b",
                query, re.IGNORECASE,
            )
            if folder_hit:
                root = folder_hit.group(1)
                query = re.sub(
                    r"\s*\bin\s+(?:my\s+|the\s+)?(downloads?|documents?|desktop|pictures?|music|videos?)\b",
                    "", query, flags=re.IGNORECASE,
                ).strip()
            query = re.sub(r"\s{2,}", " ", query).strip(" ,.")
            return Intent(
                "search_files",
                {"query": query, "extension": extension, "kind": kind, "root": root},
                0.8,
                match.group(0)[:80],
                f"Searching for {query or extension or kind or 'files'}.",
            )
        return None

    def _create_folder(self, command: str, lowered: str) -> Intent | None:
        match = re.search(r"\b(?:create|make|new)\s+(?:a\s+)?(?:new\s+)?folder\s+(?:named\s+|called\s+)?(.+)$",
                          command, re.IGNORECASE)
        if not match:
            return None
        name = match.group(1).strip(" .")
        parent = None
        if re.search(r"\bin (my )?documents\b", lowered):
            parent = "Documents"
        elif re.search(r"\bin (my )?downloads\b", lowered):
            parent = "Downloads"
        elif re.search(r"\bon (my )?desktop\b", lowered):
            parent = "Desktop"
        name = re.sub(r"\s+in\s+(my\s+)?(documents|downloads|desktop)\b.*$", "", name).strip()
        return Intent(
            "create_folder", {"name": name, "parent": parent}, 0.85, match.group(0)[:80],
            f"Creating the folder {name}.",
        )

    def _open_folder_or_app(self, command: str, lowered: str) -> Intent | None:
        match = re.search(r"\b(?:open|launch|start|run|show me)\s+(.+)$", command, re.IGNORECASE)
        if not match:
            return None
        target = match.group(1).strip(" .")
        target = re.sub(r"^(my|the|up)\s+", "", target, flags=re.IGNORECASE)
        if not target:
            return None
        if re.match(r"^(https?://|www\.)", target):
            return Intent("open_url", {"url": target}, 0.95, target, f"Opening {target}.")
        folder_match = re.match(
            r"^(documents?|docs|downloads?|desktop|pictures?|photos?|music|videos?|movies?|home)\s*(folder)?$",
            target, re.IGNORECASE,
        )
        if folder_match:
            alias = folder_match.group(1).lower()
            if alias == "docs":
                alias = "documents"
            return Intent(
                "open_folder",
                {"folder": FOLDER_ALIASES.get(alias, alias)},
                0.9,
                target,
                f"Opening your {alias} folder.",
            )
        if re.search(r"\b(file|document|pdf|docx?|note)\b", target):
            return Intent("open_file", {"name": target}, 0.7, target, f"Looking for {target}.")
        return Intent(
            "open_app", {"app": target}, 0.8, target, f"Opening {target}."
        )

    def _web_search(self, command: str, lowered: str) -> Intent | None:
        compound = re.search(
            r"\bopen\s+(.+?)\s+and\s+(?:search|google|look up)\s+(?:for\s+)?(.+)$",
            command, re.IGNORECASE,
        )
        if compound:
            query = compound.group(2).strip(" ?.")
            if query:
                return Intent(
                    "web_search",
                    {"query": query, "open_browser": True},
                    0.9,
                    compound.group(0)[:80],
                    f"Opening the browser and searching for {query}.",
                )
        match = re.search(
            r"\b(?:search|google|look up|find online|search the web|search for)\b\s*(?:for\s+|the web for\s+)?(.+)$",
            command, re.IGNORECASE,
        )
        if not match:
            return None
        query = match.group(1).strip(" ?.")
        if len(query) < 2:
            return None
        return Intent(
            "web_search", {"query": query}, 0.85, match.group(0)[:80], f"Searching the web for {query}."
        )

    def _media(self, command: str, lowered: str) -> Intent | None:
        match = re.search(r"\b(?:play|put on)\s+(?:my\s+|the\s+)?(.+?)(?:\s+playlist)?$",
                          command, re.IGNORECASE)
        if not match:
            return None
        query = match.group(1).strip(" .")
        if not query or re.search(r"\b(video|game)\b", query):
            return None
        return Intent("play_media", {"query": query}, 0.75, match.group(0)[:80], f"Playing {query}.")

    def _list_state(self, command: str, lowered: str) -> Intent | None:
        if re.search(r"\b(what|show|list|any)\b.*\b(tasks?|to-?do)\b", lowered) or lowered in ("tasks", "my tasks"):
            return Intent("list_tasks", {}, 0.9, "list tasks", "Here are your tasks.")
        if re.search(r"\b(what|show|list)\b.*\b(reminders?)\b", lowered):
            return Intent("list_reminders", {}, 0.9, "list reminders", "Here are your reminders.")
        if re.search(r"\b(what|show|list)\b.*\b(schedule|calendar|events?|agenda)\b", lowered) or "my day" in lowered:
            return Intent("list_schedule", {}, 0.9, "schedule", "Here is your schedule.")
        if re.search(r"\bwhat time is it\b|\bwhat'?s the (time|date)\b", lowered):
            return Intent("time", {}, 0.95, "time", "")
        return None
