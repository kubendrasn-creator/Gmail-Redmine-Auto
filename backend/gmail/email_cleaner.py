import re
from dataclasses import dataclass, field

from backend.config.settings import AI_MAX_INPUT_CHARS, BASE_DIR, STRIP_BOILERPLATE


@dataclass
class ThreadMessage:
    role: str
    depth: int
    from_: str | None
    to: str | None
    cc: str | None
    date: str | None
    subject: str | None
    paragraphs: list[str] = field(default_factory=list)
    signature_lines: list[str] = field(default_factory=list)
    boilerplate_removed: bool = False
    image_placeholders_removed: int = 0

    @property
    def body(self):
        return "\n\n".join(self.paragraphs)


_BOUNDARY = re.compile(
    r"^\s*(?:"
    r"-{2,}\s*forwarded message\s*-{2,}|"
    r"-{2,}\s*original message\s*-{2,}|"
    r"begin forwarded message:?|"
    r"on .{5,}(?:wrote:)?|"
    r"_{10,}"
    r")\s*$",
    re.IGNORECASE,
)
_HEADER = re.compile(r"^\s*(From|Sent|Date|To|Cc|Subject)\s*:\s*(.*)$", re.I)
_BOILERPLATE_FILE = BASE_DIR / "env" / "config" / "boilerplate_patterns.yaml"


def _boilerplate_patterns():
    if not STRIP_BOILERPLATE or not _BOILERPLATE_FILE.exists():
        return ()
    patterns = []
    for line in _BOILERPLATE_FILE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*-\s*(.+?)\s*$", line)
        if match:
            pattern = match.group(1).strip().strip("\"'")
            if pattern:
                patterns.append(re.compile(pattern, re.IGNORECASE))
    return tuple(patterns)


def _clean_markup(text):
    removed_images = len(
        re.findall(r"\[image\s*:[^\]]*\]|\[cid\s*:[^\]]*\]", text, re.I)
    )
    text = re.sub(r"\[image\s*:[^\]]*\]|\[cid\s*:[^\]]*\]", "", text, flags=re.I)
    text = text.replace("%20", " ")
    text = re.sub(r"<(?:tel|mailto):[^>]+>", "", text, flags=re.I)
    text = re.sub(r"<([^<>]+@[^<>]+)>", r"(\1)", text)
    text = re.sub(r"\*([^*\n]+)\*", r"\1", text)
    return text, removed_images


def _reflow(lines):
    paragraphs = []
    current = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        if re.match(r"^(?:[-*•]|\d+[.)])\s+", stripped):
            if current:
                paragraphs.append(" ".join(current))
                current = []
            paragraphs.append(stripped)
        else:
            current.append(stripped)
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def parse_thread(text_plain):
    text, image_count = _clean_markup(text_plain or "")
    lines = text.splitlines()
    chunks = []
    current = []
    role = "latest"
    depth = 0

    def flush():
        nonlocal current
        if current:
            chunks.append((role, depth, current))
            current = []

    for line_index, line in enumerate(lines):
        stripped = line.strip()
        quote_match = re.match(r"^(>+)\s?(.*)$", line)
        outlook_header = (
            re.match(r"^\s*From\s*:", line, re.I)
            and any(
                re.match(r"^\s*(?:Sent|Date|To|Cc|Subject)\s*:", candidate, re.I)
                for candidate in lines[line_index + 1:line_index + 6]
            )
        )
        if outlook_header and current:
            flush()
            role = "forwarded"
            depth = max(1, depth + 1)
        if _BOUNDARY.match(stripped):
            flush()
            role = "forwarded" if "forward" in stripped.lower() else "quoted"
            depth = max(1, depth + 1)
            continue
        if quote_match:
            quote_depth = len(quote_match.group(1))
            if role == "latest" or (current and depth != quote_depth):
                flush()
                role = "quoted"
            depth = quote_depth
            current.append(quote_match.group(2))
            continue
        if current and role == "quoted" and not stripped:
            current.append("")
            continue
        if role == "quoted" and not current and not stripped:
            continue
        current.append(line)
    flush()
    if not chunks:
        chunks = [("latest", 0, lines)]

    result = []
    for index, (chunk_role, chunk_depth, chunk_lines) in enumerate(chunks):
        headers = {}
        body_lines = []
        collecting_headers = True
        for line in chunk_lines:
            match = _HEADER.match(line)
            if collecting_headers and match:
                headers[match.group(1).lower()] = match.group(2).strip()
                continue
            if collecting_headers and not line.strip() and headers:
                collecting_headers = False
                continue
            collecting_headers = False
            body_lines.append(line)

        boilerplate_positions = [
            position
            for pattern in _boilerplate_patterns()
            for position, line in enumerate(body_lines)
            if pattern.search(line)
        ]
        footer_removed = bool(boilerplate_positions)
        if footer_removed:
            body_lines = body_lines[:min(boilerplate_positions)]

        signature_lines = []
        signature_index = next(
            (i for i, line in enumerate(body_lines) if line.strip() in {"--", "-- "}),
            None,
        )
        if signature_index is None:
            signature_markers = re.compile(
                r"^\s*(?:regards|best(?: regards)?|thanks|thank you|sincerely|kind regards),?\s*$",
                re.IGNORECASE,
            )
            signature_index = next(
                (
                    index
                    for index, line in enumerate(body_lines[-8:], start=max(0, len(body_lines) - 8))
                    if signature_markers.match(line)
                    and any(item.strip() for item in body_lines[index + 1:])
                ),
                None,
            )
        if signature_index is not None:
            signature_lines = [line.strip() for line in body_lines[signature_index + 1:] if line.strip()]
            body_lines = body_lines[:signature_index]

        result.append(
            ThreadMessage(
                role=chunk_role if index else "latest",
                depth=chunk_depth,
                from_=headers.get("from"),
                to=headers.get("to"),
                cc=headers.get("cc"),
                date=headers.get("date") or headers.get("sent"),
                subject=headers.get("subject"),
                paragraphs=_reflow(body_lines),
                signature_lines=signature_lines,
                boilerplate_removed=footer_removed,
                image_placeholders_removed=image_count if index == 0 else 0,
            )
        )
    return result


def build_ai_input(thread, max_chars=None):
    if isinstance(thread, str):
        messages = parse_thread(thread)
    else:
        messages = list(thread or [])
    if not messages:
        return ""
    latest = next((message for message in messages if message.role == "latest"), messages[0])
    origin = next(
        (message for message in reversed(messages) if message.role in {"forwarded", "quoted"}),
        None,
    )
    selected = [latest] + ([origin] if origin and origin is not latest else [])
    text = "\n\n".join(
        f"{message.subject + chr(10) if message.subject else ''}{message.body}"
        for message in selected
        if message.body or message.subject
    )
    limit = max_chars or AI_MAX_INPUT_CHARS
    if len(text) <= limit:
        return text
    truncated = text[:limit]
    boundary = max(truncated.rfind("\n"), truncated.rfind(" "))
    return truncated[:boundary] if boundary > 0 else truncated
