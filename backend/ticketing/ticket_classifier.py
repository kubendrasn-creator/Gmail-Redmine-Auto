from typing import Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from backend.config.settings import GEMINI_API_KEY
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
    ticket_type: Optional[str] = Field(
        default="Support",
        description="Category of ticket (e.g. 'Support', 'Bug', 'Access Request').",
    )
    center: Optional[str] = Field(
        default=None,
        description="Extracted facility/center name if present, else null.",
    )
    module: Optional[str] = Field(
        default=None,
        description="Extracted system module or component if present, else null.",
    )
    description: Optional[str] = Field(
        default=None,
        description="Concise 1-2 sentence summary of the core issue reported.",
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
"""


class TicketClassifier:
    """
    AI-powered email classifier using Gemini 2.5 Flash.
    Determines whether an email should become a Redmine Support ticket.
    """

    def __init__(self):
        if not GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY not set. AI classification will fail gracefully.")
        self.client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

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

        user_prompt = f"""
Please analyze the following incoming email:

SENDER: {sender}
SUBJECT: {subject}

EMAIL BODY:
---
{body}
---
"""

        try:
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

            result = EmailClassificationResult.model_validate_json(response.text)

            logger.info(
                f"AI Classification: is_ticket={result.is_ticket}, "
                f"type={result.ticket_type}, reasoning={result.reasoning}"
            )

            # Convert to the format expected by the existing workflow
            return {
                "is_ticket": result.is_ticket,
                "ticket_type": result.ticket_type or "Support",
                "matched_keywords": [],  # Not used in AI approach
                "structured_feedback": False,  # Not used in AI approach
                "ai_classification": True,
                "center": result.center,
                "module": result.module,
                "description": result.description,
                "reasoning": result.reasoning,
            }

        except Exception as e:
            logger.error(f"Gemini classification failed: {e}", exc_info=True)
            logger.info("Falling back to rule-based classification.")
            return self._fallback_classification(email)

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
            "center": None,
            "module": None,
            "description": None,
            "reasoning": "Fallback to rule-based classification due to AI error",
        }
