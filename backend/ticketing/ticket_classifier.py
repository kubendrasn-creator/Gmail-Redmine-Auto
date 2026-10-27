import hashlib
import re
from typing import Callable, Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from backend.config.settings import (
    AI_MAX_INPUT_CHARS,
    ALLOWED_MODULES,
    AI_THINKING_BUDGET,
    DEFAULT_ASSIGNEE_ID,
    GEMINI_API_KEY,
)
from backend.ticketing.assignees import resolve_assignee
from backend.utils.logger import get_logger

logger = get_logger("classifier")

_CENTER_PLACEHOLDERS = {
    "unknown",
    "unknown center",
    "n/a",
    "na",
    "not provided",
    "none",
}
_CENTER_CREATION_PATTERN = re.compile(
    r"\b(?:new|creat\w*|add\w*|set\s+up|setup|establish\w*|"
    r"onboard\w*|register\w*|configur\w*)\b.{0,100}\b"
    r"(?:center|centre|facility|site|location)\b"
    r"|\b(?:center|centre|facility|site|location)\b.{0,100}\b"
    r"(?:creat\w*|add\w*|set\s+up|setup|establish\w*|"
    r"onboard\w*|register\w*|configur\w*)\b",
    re.IGNORECASE | re.DOTALL,
)


def normalize_center(raw):
    value = re.sub(r"\s+", " ", str(raw or "")).strip()
    value = value.strip(" \t\r\n\"'[](){}<>")
    value = value.rstrip(".,;:!?")
    if not value or value.casefold() in _CENTER_PLACEHOLDERS:
        return ""
    if value.islower() or value.isupper():
        value = value.title()
    return value


class EmailClassificationResult(BaseModel):
    """Schema for AI-based email classification results."""

    is_ticket: bool = Field(
        description=(
            "True if the email represents a support ticket or issue requiring"
            " technical/operational action, False otherwise."
        )
    )
    category: Literal[
        "issue",
        "question",
        "noise",
        "followup",
        "center_creation",
    ] = Field(
        description=(
            "Classification category. Use center_creation for requests to create, "
            "add, register, onboard, set up, or configure a new center or facility."
        ),
    )
    issue_evidence: str = Field(
        default="",
        description="Verbatim quote from email supporting that an issue exists, or empty string if none",
    )
    center: str = Field(
        default="",
        description="Extracted facility/center name if present, else empty string.",
    )
    enterprise: str = Field(
        default="",
        description="Extracted enterprise or customer group if present, else empty string.",
    )
    module: str = Field(
        default="",
        description="Extracted system module or component if present, else empty string.",
    )
    ticket_type: Literal["support", "feedback"] = Field(
        default="support",
        description="Use feedback for explicit product feedback; otherwise support.",
    )
    assignee_name: str = Field(
        default="",
        description="Person explicitly asked to own or act on the work, or empty string.",
    )
    assignee_evidence: str = Field(
        default="",
        description="Verbatim body sentence naming the requested assignee, or empty string.",
    )
    subject: str = Field(
        description=(
            "Concise subject <= 80 chars. Prefix with [Center] only when a real "
            "center is identified; otherwise omit the center prefix."
        ),
    )
    description: str = Field(
        description="Structured description with sections for Redmine",
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description="List of required fields that are missing",
    )
    attachment_dependent: bool = Field(
        default=False,
        description="True if reporter relies on attachment/link with no written explanation",
    )
    confidence: float = Field(
        default=0.0,
        description="Confidence score 0-1, used as tiebreaker only",
    )
    reasoning: str = Field(
        description=(
            "1-sentence explanation of why this email was or was not classified"
            " as a ticket."
        )
    )


class SubjectTriageResult(BaseModel):
    """Conservative first-pass routing based on the subject line only."""

    decision: Literal["noise", "inspect_body"] = Field(
        description=(
            "'noise' only when the subject is clearly and unambiguously "
            "non-actionable; otherwise 'inspect_body'."
        )
    )
    confidence: float = Field(default=0.0, description="Confidence score from 0 to 1.")
    reasoning: str = Field(description="Short reason based only on the subject.")


SUBJECT_TRIAGE_PROMPT = """
You are the first-stage email subject triage for an IT support ticket workflow.
You receive only the subject line. You do not receive the email body, sender, or attachments.

Return decision="noise" ONLY when the subject alone makes it unmistakably
non-actionable, such as an explicit thank-you/resolution, greeting, personal
chatter, newsletter, marketing message, or meeting invitation.

Return decision="inspect_body" for every possible support issue, request, follow-up,
ambiguous/generic subject, forwarded message, status report, or anything that could
reasonably need technical or operational action. When uncertain, inspect the body.
Do not assume a short or vague subject is noise.
"""


CLASSIFICATION_SYSTEM_PROMPT = """
You are an intelligent Email Classifier and Data Extraction Agent for an enterprise IT/Helpdesk ticketing system (Redmine).

YOUR GOAL:
Analyze the subject line, sender, and body of an incoming email to determine if it represents an actionable support ticket, and extract key metadata.

CLASSIFICATION RULES:

1. SET `is_ticket = true` IF:
   - The email reports a software bug, technical error, system failure, broken feature, or data sync problem (e.g. HST upload issues, EOC round audit blockages, login errors).
   - The email asks for technical support, troubleshooting, system investigation, or operational help—even if written casually or forwarded (e.g., "Fwd: EOC Round Audits", "It's acting weird").
   - The email contains structured support fields (such as Center:, Module:, Description:, Issue:, Steps to Reproduce:).

2. SET `is_ticket = false` IF:
   - It is marketing, promotional material, spam, or a newsletter.
   - It is general company announcements, HR updates, or calendar meeting invitations.
   - It is personal chatter (e.g., "Lunch tomorrow?").
   - It is a simple "Thank you" or confirmation message (e.g., "Thanks, this is resolved now") without a new issue reported.

FIELD EXTRACTION:
- Extract `center`, `enterprise`, `module`, and `description` if explicitly stated OR clearly inferrable from the email body.
- If `issue_evidence`, `center`, or `module` is not present, return an empty string for that field.
- If the center is not identified, do not mention it in the subject or description. Never use placeholders such as "[Unknown]", "Unknown", or "N/A".
- Extract a facility/site as `center` and its customer group as `enterprise`. Do not mistake the enterprise for a center; code may use the enterprise as the fallback customer/location value when no center is named.
- Determine the assignee from the body only. Do not infer a Redmine ID and do not use names found only in headers, quoted "wrote:" lines, or signatures.
- The assignee is the person asked to act or own the work ("pls take up", "look into", "check", "fix", "confirm", "FYA", "follow the process"). Do not assign someone who is mentioned only for advice or reference.
- Example: "Person1 pls take up this. Person2 pls guide." -> assignee_name="Person1".
- Example: "Person A pls confirm this." -> assignee_name="Person A".
- Example: "Kubendra FYA. Pls follow the process doc." -> assignee_name="Kubendra".
- If the latest message is a support-team status/completion reply and the customer's original request is quoted or forwarded, classify and title the original request; keep the latest message as a "Latest update" with its sender.
- A request to create, add, set up, register, onboard, or configure a new center, facility, site, or location is an actionable support ticket even when no software error is reported.
- For a request to create or set up a center, set category="center_creation".
- `issue_evidence` must be a verbatim substring of the email body; do not invent evidence.
- If the email is attachment/link only with no written description, set `attachment_dependent=true`.

FEW-SHOT EXAMPLES:

Example 1 (Normal issue):
Subject: Login fails after password reset
Body: I reset my password but now I can't login. Error says "Invalid credentials".
Response: is_ticket=true, category="issue", issue_evidence="Error says \"Invalid credentials\"", attachment_dependent=false

Example 2 (Video-only):
Subject: Look at this
Body: <no text, just a video attachment>
Response: is_ticket=true, category="issue", issue_evidence=null, attachment_dependent=true

Example 3 (Noise):
Subject: Thanks for the help
Body: Thanks, this is resolved now.
Response: is_ticket=false, category="noise", issue_evidence=null, attachment_dependent=false
"""


class TicketClassifier:
    """
    AI-powered email classifier using Gemini 3.1 Flash-Lite.
    Determines whether an email should become a Redmine Support ticket.
    """

    def __init__(
        self,
        usage_callback: Callable[[dict[str, int]], None] | None = None,
    ):
        if not GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY not set. AI classification will fail gracefully.")
        self.client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
        self.usage_callback = usage_callback

    def classify(self, email):
        """
        Classify an email as a Support ticket or a normal email using AI.

        Args:
            email: Parsed email dictionary from EmailParser

        Returns:
            Dictionary with classification results compatible with existing workflow
        """
        if not self.client:
            logger.warning("Gemini client not initialized. Using fallback classification.")
            return self._fallback_classification(email)

        subject = email.get("subject", "").strip()
        usage_totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "tool_input_tokens": 0,
            "total_tokens": 0,
        }
        usage_reported = False

        try:
            import time
            if subject:
                triage_prompt = f"Subject: {subject}"
                start_time = time.time()
                triage_response = self.client.models.generate_content(
                    model="gemini-3.1-flash-lite",
                    contents=triage_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SUBJECT_TRIAGE_PROMPT,
                        response_mime_type="application/json",
                        response_schema=SubjectTriageResult,
                        thinking_config=types.ThinkingConfig(
                            include_thoughts=True,
                            thinking_budget=AI_THINKING_BUDGET,
                        ),
                        temperature=0,
                    ),
                )
                triage_usage = self._extract_token_usage(triage_response)
                if triage_usage is not None:
                    usage_reported = True
                    self._add_usage(usage_totals, triage_usage)
                triage = SubjectTriageResult.model_validate_json(triage_response.text)
                triage_latency = time.time() - start_time
                logger.debug(
                    "Subject triage: model=gemini-3.1-flash-lite, "
                    "latency=%.2fs, decision=%s",
                    triage_latency,
                    triage.decision,
                )
                if triage.decision == "noise":
                    result = self._subject_noise_result(subject, triage)
                    return self._with_token_usage(
                        result,
                        usage_totals,
                        usage_reported,
                    )

            # Do not read or submit the email body until subject triage says
            # that it could be actionable or the subject is blank/ambiguous.
            sender = email.get("sender_email", "").strip()
            body = email.get("classification_body", email.get("body", "")).strip()
            body = self._truncate_text(body, AI_MAX_INPUT_CHARS)
            user_prompt = f"""
Please analyze the following incoming email:

SENDER: {sender}
SUBJECT: {subject}

EMAIL BODY:
---
{body}
---
"""
            prompt_hash = hashlib.sha256(
                CLASSIFICATION_SYSTEM_PROMPT.encode()
            ).hexdigest()[:8]
            start_time = time.time()
            response = self.client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=CLASSIFICATION_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=EmailClassificationResult,
                    thinking_config=types.ThinkingConfig(
                        include_thoughts=True,
                        thinking_budget=AI_THINKING_BUDGET,
                    ),
                    temperature=0.1,
                ),
            )
            body_usage = self._extract_token_usage(response)
            if body_usage is not None:
                usage_reported = True
                self._add_usage(usage_totals, body_usage)
            result = EmailClassificationResult.model_validate_json(response.text)
            center_creation = bool(
                _CENTER_CREATION_PATTERN.search(f"{subject}\n{body}")
            )
            if center_creation:
                result.is_ticket = True
                result.category = "center_creation"

            latency = time.time() - start_time
            logger.debug(
                "AI body classification: model=gemini-3.1-flash-lite, "
                "prompt_hash=%s, latency=%.2fs",
                prompt_hash,
                latency,
            )
            logger.debug("AI response (truncated): %s", response.text[:4000])

            # Validate issue_evidence is in the body
            if result.issue_evidence and not self._evidence_matches(
                result.issue_evidence,
                body,
            ):
                logger.warning(f"issue_evidence not found in body, setting to None")
                result.issue_evidence = ""

            # Validate module is in allowed list
            if result.module and ALLOWED_MODULES and result.module not in ALLOWED_MODULES:
                logger.warning(f"Module '{result.module}' not in ALLOWED_MODULES, setting to None")
                result.module = ""
                result.missing_fields.append("module")

            self._omit_missing_center_placeholders(result)
            center = normalize_center(email.get("center") or result.center)
            enterprise = normalize_center(email.get("enterprise") or result.enterprise)
            center_or_enterprise = center or enterprise
            if not center_or_enterprise and "center" not in result.missing_fields:
                result.missing_fields.append("center")
            result.subject = self._subject_with_center(
                result.subject,
                center_or_enterprise,
            )
            if enterprise and enterprise.casefold() not in result.description.casefold():
                result.description = f"Enterprise: {enterprise}\n\n{result.description}".strip()

            assignee_result = resolve_assignee(
                {
                    "assignee_name": result.assignee_name,
                    "assignee_evidence": result.assignee_evidence,
                },
                email.get("classification_body", email.get("body", "")),
            )
            if result.assignee_evidence and not self._evidence_matches(
                result.assignee_evidence,
                email.get("classification_body", email.get("body", "")),
            ):
                result.assignee_name = ""
                result.assignee_evidence = ""
            # Truncate subject to 80 chars at word boundary
            result.subject = self._truncate_at_word_boundary(result.subject, 80)

            logger.info(
                f"AI Classification: is_ticket={result.is_ticket}, "
                f"category={result.category}, confidence={result.confidence:.2f}, "
                f"reasoning={result.reasoning}"
            )

            # Convert to the format expected by the existing workflow
            classification = {
                "is_ticket": result.is_ticket,
                "ticket_type": result.ticket_type if result.is_ticket else None,
                "matched_keywords": [],  # Not used in AI approach
                "structured_feedback": False,  # Not used in AI approach
                "ai_classification": True,
                "source": "ai",
                "center": center or None,
                "enterprise": enterprise or None,
                "center_or_enterprise": center_or_enterprise or None,
                "module": result.module or None,
                "assignee_name": assignee_result["assignee_name"],
                "assignee_id": assignee_result["assignee_id"],
                "assignee_source": assignee_result["assignee_source"],
                "assignee_status": assignee_result["assignee_status"],
                "assignee_note": assignee_result["assignee_note"],
                "people": assignee_result["people"],
                "allowed_modules": ALLOWED_MODULES,
                "default_assignee_id": DEFAULT_ASSIGNEE_ID,
                "description": result.description,
                "reasoning": result.reasoning,
                "suggested_subject": result.subject,
                "suggested_description": result.description,
                "category": result.category,
                "issue_evidence": result.issue_evidence or None,
                "missing_fields": result.missing_fields,
                "attachment_dependent": result.attachment_dependent,
                "confidence": result.confidence,
                "model": "gemini-3.1-flash-lite",
                "prompt_hash": prompt_hash,
            }
            return self._with_token_usage(
                classification,
                usage_totals,
                usage_reported,
            )

        except Exception as e:
            logger.error(f"Gemini classification failed: {e}", exc_info=True)
            logger.info("Falling back to rule-based classification.")
            fallback = self._fallback_classification(email)
            return self._with_token_usage(
                fallback,
                usage_totals,
                usage_reported,
            )

    def _record_token_usage(self, usage):
        if self.usage_callback:
            self.usage_callback(usage)
        logger.info(
            "Gemini token usage: input=%s output=%s thinking=%s "
            "tool_input=%s total=%s",
            usage["input_tokens"],
            usage["output_tokens"],
            usage["thinking_tokens"],
            usage["tool_input_tokens"],
            usage["total_tokens"],
        )

    def _add_usage(self, totals, usage):
        for key in totals:
            totals[key] += usage[key]
        self._record_token_usage(usage)

    @staticmethod
    def _with_token_usage(classification, totals, usage_reported):
        if usage_reported:
            classification["token_usage"] = dict(totals)
        return classification

    @staticmethod
    def _evidence_matches(evidence, body):
        normalize = lambda value: re.sub(r"\s+", " ", value or "").strip().casefold()
        return bool(evidence and normalize(evidence) in normalize(body))

    @staticmethod
    def _subject_with_center(subject, center):
        subject = re.sub(r"^\s*\[[^\]]+\]\s*", "", subject or "")
        return f"[{center[:40]}] {subject}".strip() if center else subject.strip()

    @staticmethod
    def _omit_missing_center_placeholders(result: EmailClassificationResult) -> None:
        if normalize_center(result.center):
            result.center = normalize_center(result.center)
            return

        result.center = ""
        result.subject = re.sub(
            r"^\s*\[(?:unknown(?:\s+center)?|n/?a|not\s+provided|none)\]\s*",
            "",
            result.subject,
            flags=re.IGNORECASE,
        )
        result.description = "\n".join(
            line
            for line in result.description.splitlines()
            if not re.match(
                r"^\s*(?:[-*]\s*)?center\s*:\s*"
                r"(?:unknown(?:\s+center)?|n/?a|not\s+provided|none)\s*[.!]?\s*$",
                line,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _subject_noise_result(subject, triage):
        return {
            "is_ticket": False,
            "ticket_type": None,
            "matched_keywords": [],
            "structured_feedback": False,
            "ai_classification": True,
            "source": "ai_subject",
            "center": None,
            "module": None,
            "description": "",
            "reasoning": triage.reasoning,
            "suggested_subject": subject[:80],
            "suggested_description": "",
            "category": "noise",
            "issue_evidence": None,
            "missing_fields": [],
            "attachment_dependent": False,
            "confidence": triage.confidence,
        }

    @staticmethod
    def _extract_token_usage(response) -> dict[str, int] | None:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return None

        def read_count(name):
            if isinstance(usage, dict):
                value = usage.get(name)
                if value is None:
                    camel_name = "".join(
                        part.title() if index else part
                        for index, part in enumerate(name.split("_"))
                    )
                    value = usage.get(camel_name)
            else:
                value = getattr(usage, name, None)
            return max(0, int(value or 0))

        input_tokens = read_count("prompt_token_count")
        output_tokens = read_count("candidates_token_count")
        thinking_tokens = read_count("thoughts_token_count")
        tool_input_tokens = read_count("tool_use_prompt_token_count")
        total_tokens = read_count("total_token_count")
        if total_tokens == 0:
            total_tokens = (
                input_tokens + output_tokens + thinking_tokens + tool_input_tokens
            )
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "thinking_tokens": thinking_tokens,
            "tool_input_tokens": tool_input_tokens,
            "total_tokens": total_tokens,
        }

    def _fallback_classification(self, email):
        """
        Fallback to simple rule-based classification if AI fails.
        Preserves original logic as safety net.
        """
        subject = email.get("subject", "").strip()
        body = email.get("classification_body", email.get("body", "")).strip()

        subject_lower = subject.lower()
        body_lower = body.lower()

        # Simple keyword matching as fallback
        SUBJECT_KEYWORDS = [
            "issue", "bug", "error", "problem", "defect",
            "incorrect", "not working", "doesn't work", "does not work",
            "unable", "failure",
        ]
        FEEDBACK_FIELDS = ["module:", "description:"]

        subject_matches = [
            keyword for keyword in SUBJECT_KEYWORDS
            if keyword in subject_lower
        ]
        center_creation = bool(
            _CENTER_CREATION_PATTERN.search(f"{subject}\n{body}")
        )

        is_structured_feedback = all(field in body_lower for field in FEEDBACK_FIELDS)
        if center_creation:
            subject_matches.append("center creation")
        is_ticket = is_structured_feedback or bool(subject_matches)
        center = normalize_center(email.get("center"))
        enterprise = normalize_center(email.get("enterprise"))
        center_or_enterprise = center or enterprise
        assignee_result = resolve_assignee({}, body)
        suggested_subject = (
            self._subject_with_center(subject, center_or_enterprise)
            if center_or_enterprise
            else subject
        )
        description = (
            f"Enterprise: {enterprise}\n\n"
            if enterprise and enterprise.casefold() not in body_lower
            else ""
        )

        logger.info(
            f"Fallback Classification: is_ticket={is_ticket}, "
            f"type={'Support' if is_ticket else None}"
        )

        return {
            "is_ticket": is_ticket,
            "ticket_type": "support" if is_ticket else None,
            "matched_keywords": subject_matches,
            "structured_feedback": is_structured_feedback,
            "ai_classification": False,
            "source": "fallback",
            "center": center or None,
            "enterprise": enterprise or None,
            "center_or_enterprise": center_or_enterprise or None,
            "module": None,
            "assignee_name": assignee_result["assignee_name"],
            "assignee_id": assignee_result["assignee_id"],
            "assignee_source": assignee_result["assignee_source"],
            "assignee_status": assignee_result["assignee_status"],
            "assignee_note": assignee_result["assignee_note"],
            "people": assignee_result["people"],
            "allowed_modules": ALLOWED_MODULES,
            "default_assignee_id": DEFAULT_ASSIGNEE_ID,
            "description": description or None,
            "reasoning": "Fallback to rule-based classification due to AI error",
            "suggested_subject": suggested_subject if is_ticket else None,
            "suggested_description": description or None,
            "category": (
                "center_creation"
                if center_creation
                else "issue" if is_ticket else "noise"
            ),
            "issue_evidence": None,
            "missing_fields": [],
            "attachment_dependent": False,
            "confidence": 0.5,
        }

    def _truncate_text(self, text: str, max_chars: int) -> str:
        """
        Truncate text at sentence or paragraph boundaries.
        Never truncates mid-word.
        """
        if len(text) <= max_chars:
            return text

        # Try to truncate at sentence boundary
        truncated = text[:max_chars]
        last_period = truncated.rfind(".")
        if last_period > max_chars * 0.8:  # Period is in the last 20%
            return text[:last_period + 1]

        # Try to truncate at paragraph boundary
        last_newline = truncated.rfind("\n")
        if last_newline > max_chars * 0.8:
            return text[:last_newline]

        # Fallback: truncate at word boundary
        last_space = truncated.rfind(" ")
        if last_space > max_chars * 0.8:
            return text[:last_space]

        # Last resort: truncate at max_chars (shouldn't happen often)
        return text[:max_chars]

    @staticmethod
    def _truncate_at_word_boundary(text: str, max_chars: int) -> str:
        """
        Truncate text at word boundary to max_chars.
        """
        if len(text) <= max_chars:
            return text

        truncated = text[:max_chars]
        last_space = truncated.rfind(" ")
        if last_space > 0:
            return text[:last_space]

        return truncated


def decide(classification: dict, context: dict) -> str:
    """
    Decide the suggested action based on classification and context.

    Returns one of: CREATE, REVIEW, NEEDS_INFO, IGNORE
    """
    category = classification.get("category", "noise")
    is_ticket = classification.get("is_ticket", False)
    attachment_dependent = classification.get("attachment_dependent", False)
    issue_evidence = classification.get("issue_evidence")
    missing_fields = classification.get("missing_fields", [])
    confidence = classification.get("confidence", 0.0)
    is_duplicate = context.get("is_duplicate", False)
    is_reply = context.get("is_reply", False)
    sender_allowed = context.get("sender_allowed", True)

    # Ignore noise and questions
    if category in ("noise", "question"):
        return "IGNORE"

    # Duplicate or reply -> review/attach
    if is_duplicate or is_reply:
        return "ATTACH_OR_REVIEW"

    # Attachment dependent or no evidence -> needs info
    if attachment_dependent or not issue_evidence:
        return "NEEDS_INFO"

    # Missing fields or untrusted sender -> review
    if missing_fields or not sender_allowed:
        return "REVIEW"

    # Low confidence -> review
    if confidence < 0.85:
        return "REVIEW"

    # Default: create
    return "CREATE"
