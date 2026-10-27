import json
import re
import string

from backend.config.settings import (
    ASSIGNEE_ACTION_WORDS,
    DEFAULT_ASSIGNEE_ID,
    PEOPLE,
    PEOPLE_FILE,
)


def _normalized_name(value):
    return "".join(
        character
        for character in value.casefold()
        if character not in string.punctuation and not character.isspace()
    )


def load_people():
    people = {}
    if PEOPLE_FILE.exists():
        loaded = json.loads(PEOPLE_FILE.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"{PEOPLE_FILE} must contain a name-to-user-ID object")
        people.update(loaded)
    for pair in PEOPLE.split(","):
        if "=" not in pair:
            continue
        name, user_id = pair.split("=", 1)
        name = name.strip()
        if name and user_id.strip().isdigit():
            people[name] = int(user_id.strip())
    validated = {}
    for name, user_id in people.items():
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            validated[name.strip()] = int(user_id)
        except (TypeError, ValueError):
            continue
    return validated


def _resolve_name(name, people):
    if not name:
        return None
    normalized = _normalized_name(name)
    exact = [
        (known_name, user_id)
        for known_name, user_id in people.items()
        if _normalized_name(known_name) == normalized
    ]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None
    name_tokens = set(re.findall(r"\w+", name.casefold()))
    partial_matches = [
        (known_name, user_id)
        for known_name, user_id in people.items()
        if (
            normalized in _normalized_name(known_name)
            or _normalized_name(known_name) in normalized
            or bool(
                name_tokens
                & {
                    token
                    for token in re.findall(r"\w+", known_name.casefold())
                    if len(token) >= 3
                }
            )
        )
    ]
    return partial_matches[0] if len(partial_matches) == 1 else None


def _evidence_in_body(evidence, body):
    compact = lambda value: re.sub(r"\s+", " ", value).strip().casefold()
    return bool(evidence and compact(evidence) in compact(body))


def _rule_candidates(body, people):
    action_words = tuple(word.casefold() for word in ASSIGNEE_ACTION_WORDS)
    action = {}
    for clause in re.split(r"(?<=[.!?;])\s+|\n+", body):
        lowered = clause.casefold()
        clause_tokens = set(re.findall(r"\w+", lowered))
        found = []
        for name, user_id in people.items():
            name_tokens = re.findall(r"\w+", name.casefold())
            if (
                re.search(rf"(?<!\w){re.escape(name)}(?!\w)", clause, re.I)
                or any(
                    len(token) >= 3 and token in clause_tokens
                    for token in name_tokens
                )
            ):
                found.append((name, user_id))
        if not found:
            continue
        has_action = any(word in lowered for word in action_words)
        if has_action:
            action.update(found)
    return action


def resolve_assignee(classification, body):
    people = load_people()
    named = classification.get("assignee_name")
    evidence = classification.get("assignee_evidence")
    source = "ai"
    status = "validated"
    resolved = None

    if _evidence_in_body(evidence, body):
        resolved = _resolve_name(named, people)
    if resolved is None:
        action = _rule_candidates(body, people)
        if len(action) == 1:
            resolved = next(iter(action.items()))
            source = "rule"
            status = "suggested"

    if resolved is None:
        return {
            "assignee_name": "",
            "assignee_id": DEFAULT_ASSIGNEE_ID or None,
            "assignee_source": "default",
            "assignee_status": "suggested",
            "assignee_note": "No assignee named in the email.",
            "people": people,
        }

    return {
        "assignee_name": resolved[0],
        "assignee_id": resolved[1],
        "assignee_source": source,
        "assignee_status": status,
        "assignee_note": "",
        "people": people,
    }
