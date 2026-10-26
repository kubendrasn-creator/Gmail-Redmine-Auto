import hashlib
from typing import Callable, Optional, Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from backend.config.settings import (
    AI_MAX_INPUT_CHARS,
    ALLOWED_MODULES,
    GEMINI_API_KEY,
)
from backend.utils.logger import get_logger

logger = get_logger("classifier")


class EmailClassificationResult(BaseModel):
    """Schema for AI-based email classification results."""

    is_ticket: bool = Field(
        description=(
            "True if the email represents a support ticket or issue requiring"
            " technical/operational action, False otherwise."
        )
    )
    category: Literal["issue", "question", "noise", "followup"] = Field(
        description="Classification category: issue, question, noise, or followup",
    )
    issue_evidence: Optional[str] = Field(
        default=None,
        description="Verbatim quote from email supporting that an issue exists",
    )
    center: Optional[str] = Field(
        default=None,
        description="Extracted facility/center name if present, else null.",
    )
    module: Optional[str] = Field(
        default=None,
        description="Extracted system module or component if present, else null.",
    )
    subject: str = Field(
        description="Concise subject <= 80 chars, format: [Center] Module: problem",
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
- Extract `center`, `module`, and `description` if explicitly stated OR clearly inferrable from the email body.
- If not present, return `null` for those fields.
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
        sender = email.get("sender_email", "").strip()
        body = email.get("classification_body", email.get("body", "")).strip()

        # Truncate body to max input chars at sentence boundaries
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

        prompt_hash = hashlib.sha256(CLASSIFICATION_SYSTEM_PROMPT.encode()).hexdigest()[:8]
        token_usage = None

        try:
            import time
            start_time = time.time()

            response = self.client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=CLASSIFICATION_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=EmailClassificationResult,
                    temperature=0.1,
                ),
            )

            token_usage = self._extract_token_usage(response)
            if token_usage is not None:
                if self.usage_callback:
                    self.usage_callback(token_usage)
                logger.info(
                    "Gemini token usage: input=%s output=%s thinking=%s "
                    "tool_input=%s total=%s",
                    token_usage["input_tokens"],
                    token_usage["output_tokens"],
                    token_usage["thinking_tokens"],
                    token_usage["tool_input_tokens"],
                    token_usage["total_tokens"],
                )

            latency = time.time() - start_time
            result = EmailClassificationResult.model_validate_json(response.text)

            logger.debug(f"AI call: model=gemini-3.1-flash-lite, prompt_hash={prompt_hash}, latency={latency:.2f}s")
            logger.debug(f"AI response (truncated): {response.text[:4000]}")

            # Validate issue_evidence is in the body
            if result.issue_evidence and result.issue_evidence not in body:
                logger.warning(f"issue_evidence not found in body, setting to None")
                result.issue_evidence = None

            # Validate module is in allowed list
            if result.module and ALLOWED_MODULES and result.module not in ALLOWED_MODULES:
                logger.warning(f"Module '{result.module}' not in ALLOWED_MODULES, setting to None")
                result.module = None
                result.missing_fields.append("module")

            # Truncate subject to 80 chars at word boundary
            result.subject = self._truncate_at_word_boundary(result.subject, 80)

            logger.info(
                f"AI Classification: is_ticket={result.is_ticket}, "
                f"category={result.category}, confidence={result.confidence:.2f}, "
                f"reasoning={result.reasoning}"
            )

            # Convert to the format expected by the existing workflow
            return {
                "is_ticket": result.is_ticket,
                "ticket_type": result.category if result.is_ticket else None,
                "matched_keywords": [],  # Not used in AI approach
                "structured_feedback": False,  # Not used in AI approach
                "ai_classification": True,
                "source": "ai",
                "center": result.center,
                "module": result.module,
                "description": result.description,
                "reasoning": result.reasoning,
                "suggested_subject": result.subject,
                "suggested_description": result.description,
                "category": result.category,
                "issue_evidence": result.issue_evidence,
                "missing_fields": result.missing_fields,
                "attachment_dependent": result.attachment_dependent,
                "confidence": result.confidence,
                "token_usage": token_usage,
            }

        except Exception as e:
            logger.error(f"Gemini classification failed: {e}", exc_info=True)
            logger.info("Falling back to rule-based classification.")
            fallback = self._fallback_classification(email)
            if token_usage is not None:
                fallback["token_usage"] = token_usage
            return fallback

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

        FEEDBACK_FIELDS = ["center:", "module:", "description:"]

        subject_matches = [
            keyword for keyword in SUBJECT_KEYWORDS
            if keyword in subject_lower
        ]

        is_structured_feedback = all(field in body_lower for field in FEEDBACK_FIELDS)
        is_ticket = is_structured_feedback or bool(subject_matches)

        logger.info(
            f"Fallback Classification: is_ticket={is_ticket}, "
            f"type={'Support' if is_ticket else None}"
        )

        return {
            "is_ticket": is_ticket,
            "ticket_type": "Support" if is_ticket else None,
            "matched_keywords": subject_matches,
            "structured_feedback": is_structured_feedback,
            "ai_classification": False,
            "source": "fallback",
            "center": None,
            "module": None,
            "description": None,
            "reasoning": "Fallback to rule-based classification due to AI error",
            "suggested_subject": subject if is_ticket else None,
            "suggested_description": None,
            "category": "issue" if is_ticket else "noise",
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

    def _truncate_at_word_boundary(self, text: str, max_chars: int) -> str:
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
