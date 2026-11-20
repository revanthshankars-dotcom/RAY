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

# Sites people name instead of typing a URL: "open youtube" means the web page,
# not a desktop shortcut that may not exist.
SITE_ALIASES = {
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "google maps": "https://maps.google.com",
    "maps": "https://maps.google.com",
    "github": "https://github.com",
    "reddit": "https://www.reddit.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "stack overflow": "https://stackoverflow.com",
    "stackoverflow": "https://stackoverflow.com",
    "x": "https://x.com",
    "twitter": "https://x.com",
    "linkedin": "https://www.linkedin.com",
    "instagram": "https://www.instagram.com",
    "whatsapp": "https://web.whatsapp.com",
    "netflix": "https://www.netflix.com",
    "drive": "https://drive.google.com",
    "calendar": "https://calendar.google.com",
    "news": "https://news.google.com",
}

# Panels RAY can open through the OS (see computer/controls.py).
PANEL_ALIASES = {
    "wifi": "network", "wi-fi": "network", "network": "network", "internet": "network",
    "bluetooth": "bluetooth", "display": "display", "screen": "display",
    "sound": "sound", "audio": "sound", "volume settings": "sound",
    "privacy": "privacy", "update": "windows_update", "windows update": "windows_update",
    "storage": "storage", "power": "power", "battery": "power",
    "apps": "apps", "about": "about", "settings": "settings",
    "task manager": "task_manager", "terminal": "terminal", "control panel": "control_panel",
}

CAPABILITIES_TEXT = """I run on this PC and understand plain English. Try things like:

Open and control
• "open chrome", "go to youtube", "open my downloads"
• "turn the volume up", "set volume to 30", "take a screenshot"
• "lock my pc", "open bluetooth settings", "close notepad"

Files
• "find my tax pdf", "search my files for invoice", "find big files"
• "organize my downloads", "back up my documents"
• "make a folder called invoices in documents"

Your day
• "remind me to call mom at 7pm", "remind me in 20 minutes to stretch"
• "add a task to buy milk", "how many tasks do I have"
• "schedule a meeting tomorrow at 4pm", "what's on my calendar"
• "make a note that the wifi password is on the fridge"

This machine
• "how's my cpu", "how much ram am i using", "how much disk space is left"
• "what's using my cpu", "show processes"
• "index my documents", "index status", "start a focus session for 25 minutes"

Memory and web
• "remember that I prefer dark mode", "what do you remember about me"
• "search the web for python asyncio", "research cheap SSDs"

Long questions and multi-step jobs need an AI provider: add a Gemini key in
Settings > AI providers (or set RAY_GEMINI_API_KEY)."""

HELP_SPEAK = "Here are the kinds of things you can ask me."



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
            self._close_app,
            self._volume,
            self._brightness,
            self._screenshot,
            self._processes,
            self._system_check,
            self._reminder,
            self._schedule_event,
            self._task,
            self._note,
            self._memory,
            self._recall,
            self._organise,
            self._backup,
            self._delete,
            self._index,
            self._system_panel,
            self._file_search,
            self._create_folder,
            self._focus,
            self._clipboard,
            self._typing,
            self._research,
            self._bookmarks,
            self._web_search,
            self._go_to,
            self._open_folder_or_app,
            self._media,
            self._list_state,
            self._limits,
            self._capabilities,
            self._smalltalk,
        ):
            intent = handler(command, lowered)
            if intent:
                return intent
        return None

    # --------------------------------------------------------------- handlers
    @staticmethod
    def _strip_time_words(text: str) -> str:
        """Remove the scheduling part of a phrase, keep the task itself.

        "in 20 minutes to stretch" -> "stretch"; "call mom at 7pm" -> "call mom".
        """
        cleaned = re.sub(
            r"\b(in|after)\s+\d+\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)\b",
            " ", text, flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b(at|by|on|before|around)\s+\d{1,2}(:\d{2})?\s*(am|pm)?\b", " ",
            cleaned, flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b(today|tonight|tomorrow|this (morning|afternoon|evening)|next "
            r"(week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
            r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
            " ", cleaned, flags=re.IGNORECASE,
        )
        return re.sub(r"\s{2,}", " ", cleaned).strip(" ,.:-")

    def _power_action(self, command: str, lowered: str) -> Intent | None:
        patterns = [
            (r"\block\b.*\b(computer|pc|laptop|screen|system|windows|it|now)\b", "lock", "Locking the computer."),
            (r"\block\s+my\b", "lock", "Locking the computer."),
            (r"\b(put\s+(my|the)\s+(pc|computer|laptop)\s+to\s+)?(sleep|hibernate)\b", "sleep",
             "Putting the computer to sleep."),
            (r"\b(restart|reboot)\b", "restart", "Restarting. This needs your explicit approval."),
            (r"\b(shut\s*down|shutdown|power\s*off|turn\s+off\s+the\s+(pc|computer|laptop))\b", "shutdown",
             "Shutting down. This needs your explicit approval."),
        ]
        for pattern, action, speak in patterns:
            if re.search(pattern, lowered):
                return Intent(f"power_{action}", {"action": action}, 0.95, pattern, speak)
        return None

    def _volume(self, command: str, lowered: str) -> Intent | None:
        exact = re.search(
            r"\b(volume|sound|audio)\b[^.\d]{0,18}?(\d{1,3})\s*%?", lowered
        ) or re.search(r"\b(volume|sound)\s+(?:to\s+)?(\d{1,3})\b", lowered)
        if exact:
            level = int(exact.group(2))
            if 0 <= level <= 100:
                return Intent(
                    "set_volume", {"percent": level}, 0.9, exact.group(0),
                    f"Setting the volume to {level} percent.",
                )
        if re.search(r"\b(full|max|maximum)\s+(volume|sound)\b|\bvolume\s+(to\s+)?(full|max)\b", lowered):
            return Intent("set_volume", {"percent": 100}, 0.9, "max volume", "Setting the volume to full.")
        for pattern, direction, speak in (
            (r"\b(louder|turn it up|turn up|volume up|increase (the )?volume|raise (the )?volume|crank (it|the volume))\b",
             "up", "Turning the volume up."),
            (r"\b(quieter|turn it down|turn down|volume down|lower (the )?volume|reduce (the )?volume|decrease (the )?volume)\b",
             "down", "Turning the volume down."),
        ):
            if re.search(pattern, lowered):
                return Intent("volume", {"direction": direction, "steps": 2}, 0.9, pattern, speak)
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
        if re.search(
            r"\b(screenshot|screen ?shot|capture (my |the )?screen|grab (my |the )?screen|"
            r"screen ?grab|capture a screenshot|take a (picture|pic) of (my |the )?screen)\b",
            lowered,
        ):
            return Intent(
                "screenshot", {}, 0.95, "screenshot", "Taking a screenshot."
            )
        return None

    def _system_check(self, command: str, lowered: str) -> Intent | None:
        signals = (
            r"\b(system report|performance report|health check|system status|system health)\b",
            r"\bhow(?:'s| is| are)\b.*\b(system|computer|laptop|pc|machine|performance)\b",
            r"\b(cpu|processor|ram|memory|battery|disk|storage|drive)\b.*\b(usage|use|load|space|left|free|full|level|percent|percentage|status)\b",
            r"\b(usage|load|space|left|free|full)\b.*\b(cpu|processor|ram|memory|battery|disk|storage|drive)\b",
            r"\bhow much (ram|memory|cpu|disk|storage|battery)\b",
            r"\b(free|available)\s+(space|memory|ram|disk)\b",
            r"\b(is my (battery|disk|memory|cpu)\b|battery low|low on (space|memory|battery))",
            r"\b(running slow|so slow|slow pc|lagging|overheat(ing)?|too hot|fan (is )?(loud|spinning))\b",
            r"\b(check|monitor|show)\s+(my\s+)?(system|pc|computer|laptop|resources|performance)\b",
        )
        for pattern in signals:
            if re.search(pattern, lowered):
                return Intent(
                    "system_report", {}, 0.9, pattern,
                    "Checking your system now.",
                )
        return None

    def _processes(self, command: str, lowered: str) -> Intent | None:
        if re.search(
            r"\b(what|which)\b.*\b(cpu|memory|ram|processor|disk)\b.*\b(us(e|ing)|eat(ing)?|hog(ging)?|consum(e|ing)|tak(e|ing))\b",
            lowered,
        ) or re.search(r"\b(what'?s|what is)\s+(using|running|eating)\b", lowered):
            sort = "memory" if re.search(r"\b(memory|ram)\b", lowered) else "cpu"
            return Intent(
                "list_processes", {"limit": 10, "sort_by": sort}, 0.85,
                "busiest processes", "Here are the busiest processes on this PC.",
            )
        if re.search(r"\b(top|show|list|check|see)\b.*\b(processes|process list|task list)\b", lowered) \
                or re.search(r"\bprocesses\b", lowered):
            return Intent(
                "list_processes", {"limit": 12, "sort_by": "cpu"}, 0.85,
                "process list", "Here are the busiest processes on this PC.",
            )
        if re.search(r"\b(what'?s|which|show|list)\b.*\b(running|open)\b.*\b(apps|applications|programs)\b", lowered) \
                or re.search(r"\b(running apps|open apps|what apps are open)\b", lowered):
            return Intent(
                "list_running_apps", {}, 0.85, "running apps", "Here is what is open right now."
            )
        return None

    def _reminder(self, command: str, lowered: str) -> Intent | None:
        match = re.search(r"\bremind me (?:to|about|that)?\s*(.+)$", command, re.IGNORECASE)
        if not match:
            return None
        remainder = match.group(1).strip()
        when = parse_when(remainder)
        title = self._strip_time_words(remainder)
        title = re.sub(r"^\s*(to|about|that)\b", "", title, flags=re.IGNORECASE).strip(" ,.:-")
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
        title = re.sub(r"^(a|an|the)\s+", "", title, flags=re.IGNORECASE).strip()
        if re.match(r"^with\b", title, re.IGNORECASE):
            title = f"Meeting {title}"
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
        title = re.sub(r"^\s*to\s+", "", title, flags=re.IGNORECASE).strip(" ,.")
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
        # "what do you remember?" is a question about memory, not a memory to store.
        if re.search(
            r"\b(what|who|which|do|did|can|could)\b.*\bremember\b|\bwhat have you (learned|remembered)\b",
            lowered,
        ):
            return None
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
            r"\b(?:search|look)\s+(?:through\s+)?(?:my\s+|the\s+)?(?:files?|folders?|documents?|pc|computer|drive)"
            r"\s+for\s+(.+)$",
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
        site = SITE_ALIASES.get(target.lower())
        if site:
            return Intent("open_url", {"url": site}, 0.9, target, f"Opening {target}.")
        if re.fullmatch(r"[\w-]+(\.[\w-]+)+\/?", target):
            return Intent("open_url", {"url": f"https://{target}"}, 0.9, target, f"Opening {target}.")
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
        query = re.sub(r"^(?:some|a bit of|a little|the)\s+", "", match.group(1).strip(" ."),
                       flags=re.IGNORECASE)
        if not query or re.search(r"\b(video|game)\b", query):
            return None
        return Intent("play_media", {"query": query}, 0.75, match.group(0)[:80], f"Playing {query}.")

    def _list_state(self, command: str, lowered: str) -> Intent | None:
        if re.search(r"\b(what|show|list|any|how many|how much)\b.*\b(tasks?|to-?do)\b", lowered) \
                or lowered in ("tasks", "my tasks", "todo", "to do"):
            return Intent("list_tasks", {}, 0.9, "list tasks", "Here are your tasks.")
        if re.search(r"\b(what|show|list)\b.*\b(reminders?)\b", lowered):
            return Intent("list_reminders", {}, 0.9, "list reminders", "Here are your reminders.")
        if re.search(r"\b(what|show|list)\b.*\b(schedule|calendar|events?|agenda)\b", lowered) or "my day" in lowered:
            return Intent("list_schedule", {}, 0.9, "schedule", "Here is your schedule.")
        if re.search(r"\bwhat time is it\b|\bwhat'?s the (time|date)\b", lowered):
            return Intent("time", {}, 0.95, "time", "")
        return None

    # ------------------------------------------------------- apps and windows
    def _close_app(self, command: str, lowered: str) -> Intent | None:
        match = re.match(
            r"^\s*(?:close|quit|exit|kill|terminate|stop)\s+(?:the\s+|my\s+)?(.+?)"
            r"(?:\s+(?:app|application|window|program|for me|please))?\s*$",
            command, re.IGNORECASE,
        )
        if not match:
            return None
        if re.search(r"\b(music|song|video|playback|movie|focus|timer|alarm|reminder)\b", lowered):
            return None
        target = match.group(1).strip(" .\"'")
        if not target or len(target) < 2 or target.lower() in ("it", "this", "that", "everything"):
            return None
        # "stop" is also a normal English word; only trust it with an app name.
        if lowered.strip().startswith("stop") and not re.search(r"\b(app|application|window|program)\b", lowered):
            return None
        return Intent(
            "close_app", {"name": target}, 0.8, match.group(0)[:80],
            f"Closing {target}. This asks for confirmation first.",
        )

    def _system_panel(self, command: str, lowered: str) -> Intent | None:
        match = re.match(
            r"^\s*(?:open|show|bring up|go to)\s+(?:the\s+|my\s+)?(.+?)\s*$",
            command, re.IGNORECASE,
        )
        if not match:
            return None
        phrase = match.group(1).strip(" .").lower()
        phrase = re.sub(r"\s+(settings?|panel|preferences|page)$", "", phrase).strip()
        panel = PANEL_ALIASES.get(phrase)
        if not panel:
            return None
        return Intent(
            "open_panel", {"panel": panel}, 0.85, match.group(0)[:80],
            f"Opening the {phrase} settings page.",
        )

    # ---------------------------------------------------- clipboard and input
    def _clipboard(self, command: str, lowered: str) -> Intent | None:
        if re.search(
            r"\b(read|show|what'?s in|what is in|open|paste|get)\b.*\bclipboard\b"
            r"|\bwhat did i (just )?copy\b",
            lowered,
        ):
            return Intent("clipboard_get", {}, 0.9, "clipboard", "Reading the clipboard.")
        match = re.search(
            r"\b(?:copy|put|place|add)\s+(?:this\s+)?[\"“']?(.+?)[\"”']?\s+"
            r"(?:to|into|on|in)\s+(?:my\s+|the\s+)?clipboard\b",
            command, re.IGNORECASE,
        )
        if not match:
            match = re.match(r"^\s*copy\s+(.+?)\s+to\s+(?:my\s+|the\s+)?clipboard\b",
                             command, re.IGNORECASE)
        if match:
            text = match.group(1).strip(" \"'")
            if text and text.lower() not in ("this", "that", "it"):
                return Intent(
                    "clipboard_set", {"text": text[:4000]}, 0.85, match.group(0)[:80],
                    "Copied to the clipboard.",
                )
        return None

    def _typing(self, command: str, lowered: str) -> Intent | None:
        if re.match(r"^\s*(?:type|type out|type this)\b", command, re.IGNORECASE):
            text = re.sub(r"^\s*(?:type out|type this|type)\s*[:\-]?\s*", "", command,
                          flags=re.IGNORECASE)
            text = re.sub(r"\s+(into|in)\s+.+$", "", text, flags=re.IGNORECASE).strip(" \"'")
            if len(text) >= 1:
                return Intent(
                    "type_text", {"text": text[:2000]}, 0.8, command[:80],
                    "Typing that into the active window. This needs your approval.",
                )
            return None
        match = re.match(
            r"^\s*(?:press|hit|send|hold)\s+(?:the\s+)?([a-z0-9+\s-]{2,30}?)\s*(?:key|keys|shortcut)?\s*$",
            command, re.IGNORECASE,
        )
        if not match:
            return None
        raw = match.group(1).strip().lower()
        parts = [part for part in re.split(r"\s*(?:\+|,|\band\b|\bthen\b)\s*", raw) if part]
        alias = {"control": "ctrl", "windows": "win", "cmd": "win", "command": "cmd",
                 "escape": "esc", "return": "enter", "del": "delete"}
        keys = [alias.get(part.strip(), part.strip()) for part in parts if part.strip()]
        if not keys or any(len(key) > 12 for key in keys):
            return None
        return Intent(
            "press_hotkey", {"keys": keys}, 0.8, command[:80],
            f"Pressing {'+'.join(keys)}. This needs your approval.",
        )

    # -------------------------------------------------- memory, index, focus
    def _recall(self, command: str, lowered: str) -> Intent | None:
        if re.search(
            r"\bwhat do you (remember|know)\b|\bwhat have you (learned|remembered)\b",
            lowered,
        ):
            return Intent("recall", {"query": ""}, 0.9, "recall", "Here is what I remember about you.")
        match = re.match(r"^\s*(?:recall|do you remember)\s+(?:about\s+)?(.+)$",
                         command, re.IGNORECASE)
        if match:
            query = match.group(1).strip(" ?.")
            if len(query) >= 2:
                return Intent("recall", {"query": query[:120]}, 0.85, match.group(0)[:80],
                              f"Looking through my memories about {query}.")
        return None

    def _index(self, command: str, lowered: str) -> Intent | None:
        if re.search(
            r"\bindex(ing)? status\b|\bwhat have you indexed\b|\bhow many files (have you )?indexed\b"
            r"|\bindex size\b|\bis my (files|documents) indexed\b",
            lowered,
        ):
            return Intent("index_status", {}, 0.9, "index status", "Checking the search index.")
        if re.search(
            r"\b(index|re-?index|scan|add)\b.*\b(files|documents|folders?|drive|pc|everything)\b",
            lowered,
        ) and not re.search(r"\b(virus|malware|network|wifi|ports?)\b", lowered):
            return Intent(
                "index_files", {}, 0.8, "index files",
                "Indexing your folders so I can search them. This asks for confirmation first.",
            )
        return None

    def _focus(self, command: str, lowered: str) -> Intent | None:
        if not re.search(r"\b(focus|pomodoro|deep work)", lowered):
            return None
        minutes = 25
        match = re.search(r"(\d{1,3})\s*(?:minutes?|mins?|m)\b", lowered)
        if match:
            minutes = max(1, min(int(match.group(1)), 240))
        label = ""
        label_match = re.search(r"\b(?:on|for)\s+([a-z0-9 ,'-]{3,40})$", command, re.IGNORECASE)
        if label_match and not re.fullmatch(r"\d+\s*(minutes?|mins?)", label_match.group(1).strip()):
            label = label_match.group(1).strip()
        return Intent(
            "start_focus", {"minutes": minutes, **({"label": label} if label else {})}, 0.85,
            "focus session", f"Starting a {minutes} minute focus session.",
        )

    # --------------------------------------------------------- web and research
    def _research(self, command: str, lowered: str) -> Intent | None:
        match = re.match(
            r"^\s*(?:research|investigate|deep dive (?:on|into)|look into|study)\s+(.+)$",
            command, re.IGNORECASE,
        )
        if not match:
            return None
        question = match.group(1).strip(" ?.")
        if len(question) < 3:
            return None
        return Intent(
            "research", {"question": question[:300], "pages": 4}, 0.85, match.group(0)[:80],
            f"Researching {question} with sources.",
        )

    def _bookmarks(self, command: str, lowered: str) -> Intent | None:
        if re.search(r"\b(show|list|what are|open|read)\b.*\bbookmarks?\b", lowered):
            return Intent("list_bookmarks", {}, 0.9, "bookmarks", "Here are your saved bookmarks.")
        return None

    def _go_to(self, command: str, lowered: str) -> Intent | None:
        match = re.match(r"^\s*(?:go to|take me to|visit|navigate to)\s+(.+)$", command, re.IGNORECASE)
        if not match:
            return None
        target = re.sub(r"^(my|the)\s+", "", match.group(1).strip(" ."), flags=re.IGNORECASE)
        if not target:
            return None
        site = SITE_ALIASES.get(target.lower())
        if site:
            return Intent("open_url", {"url": site}, 0.9, match.group(0)[:80], f"Opening {target}.")
        if re.match(r"^(https?://|www\.)", target):
            return Intent("open_url", {"url": target}, 0.95, target, f"Opening {target}.")
        if re.fullmatch(r"[\w-]+(\.[\w-]+)+\/?", target):
            return Intent("open_url", {"url": f"https://{target}"}, 0.9, target, f"Opening {target}.")
        folder = FOLDER_ALIASES.get(target.lower().replace(" folder", ""))
        if folder is not None and target.lower().endswith(("folder", "documents", "downloads", "desktop")):
            return Intent("open_folder", {"folder": folder}, 0.85, target, f"Opening your {target}.")
        return None

    # --------------------------------------------------------- help and hello
    def _capabilities(self, command: str, lowered: str) -> Intent | None:
        if re.fullmatch(
            r"\s*(help|commands|capabilities|examples|what can i ask( you)?)\s*[?.!]*",
            lowered,
        ) or re.search(
            r"\bwhat can you do\b|\bwhat are your (commands|capabilities)\b|"
            r"\bhow do i use you\b|\bshow (me )?(your )?commands\b",
            lowered,
        ):
            return Intent(
                "capabilities", {"answer": CAPABILITIES_TEXT}, 0.95, "help", HELP_SPEAK,
            )
        return None

    def _smalltalk(self, command: str, lowered: str) -> Intent | None:
        patterns = (
            (r"^(hi|hello|hey|yo|hiya|howdy|namaste|good (morning|afternoon|evening))\b[\s!.?]*$",
             "Hi - I'm RAY, running locally on this PC. Ask me to open apps, find files, set "
             "reminders or check the system. Say \"help\" for examples."),
            (r"^(thanks|thank you|thx|cheers|nice|great|cool|perfect|awesome)\b[\s!.?]*$",
             "Anytime."),
            (r"^(who are you|what are you|what'?s your name|your name)\b.*$",
             "I'm RAY - Real-time Autonomous Yourself. A local assistant that runs on your own "
             "machine: files, apps, tasks, reminders, research and system control."),
            (r"^(how are you|how'?s it going|you ok|are you (ok|there|awake|online))\b.*$",
             "Running fine and fully local. What should I do?"),
            (r"^(bye|goodbye|see you|good night|goodnight|later)\b[\s!.?]*$",
             "Talk later. Everything stays on this machine."),
            (r"^(test|testing|are you working)\b.*$",
             "I'm here and the local tools are live. Try \"open my downloads\" or \"system report\"."),
        )
        for pattern, reply in patterns:
            if re.search(pattern, lowered):
                return Intent("smalltalk", {"answer": reply}, 0.9, pattern[:40], reply)
        return None
        for pattern, reply in patterns:
            if re.search(pattern, lowered):
                return Intent("smalltalk", {"answer": reply}, 0.9, pattern[:40], reply)
        return None

    def _limits(self, command: str, lowered: str) -> Intent | None:
        """Say plainly what RAY cannot do instead of pretending or hanging."""
        cases = (
            (r"\b(turn|switch)\s+(off|on)\s+(the\s+)?(wifi|wi-fi|bluetooth|internet|airplane|flight mode)\b",
             "I can open the network or Bluetooth settings page, but I will not fake toggling the "
             "radio. Say \"open network settings\" and flip it there."),
            (r"\b(empty|clear|delete)\s+(the\s+)?(trash|recycle bin|bin)\b",
             "RAY doesn't hard-delete: files go to its own quarantine folder and stay restorable. "
             "Use Settings > Storage to review quarantine, or empty the system bin yourself."),
            (r"\b(send|write|draft)\s+(an?\s+)?(email|mail|message)\b|\bemail\s+\w+",
             "I don't have an email or messaging account connected, so I can't send that. I can "
             "draft the text into a note for you instead."),
            (r"\b(make|place|start)\s+(a\s+)?(phone )?call\b|\bcall\s+\+?\d",
             "I can't place calls - there's no telephony integration on this machine."),
            (r"\b(buy|order|purchase|book a flight|book a ticket)\b",
             "I won't make purchases or bookings. I can research options and compare prices, then "
             "you complete the order yourself."),
            (r"\b(change|set)\s+(my\s+)?wallpaper\b",
             "Changing the wallpaper isn't one of my tools yet. I can open the personalisation "
             "settings page for you."),
            (r"\b(install|uninstall|update)\s+(this\s+)?(app|program|software|windows|driver)\b",
             "I don't install, uninstall or update software - that is a system change outside my "
             "tool set. I can open the Apps settings page."),
        )
        for pattern, reply in cases:
            if re.search(pattern, lowered):
                return Intent("explain", {"answer": reply}, 0.8, pattern[:40], reply.split(".")[0] + ".")
        return None
