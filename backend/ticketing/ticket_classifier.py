class TicketClassifier:
    """
    Determines whether an email should become
    a Redmine Support ticket.
    """

    SUBJECT_KEYWORDS = [
        "issue",
        "bug",
        "error",
        "problem",
        "defect",
        "incorrect",
        "not working",
        "doesn't work",
        "does not work",
        "unable",
        "failure",
    ]

    FEEDBACK_FIELDS = [
        "center:",
        "module:",
        "description:",
    ]

    def classify(self, email):
        """
        Classify an email as a Support ticket or
        a normal email.
        """

        subject = email.get("subject", "").strip()
        body = email.get("body", "").strip()

        subject_lower = subject.lower()
        body_lower = body.lower()

        subject_matches = self._find_subject_keywords(
            subject_lower
        )

        is_structured_feedback = self._is_structured_feedback(
            body_lower
        )

        is_ticket = bool(
            subject_matches or is_structured_feedback
        )

        return {
            "is_ticket": is_ticket,
            "ticket_type": "Support" if is_ticket else None,
            "matched_keywords": subject_matches,
            "structured_feedback": is_structured_feedback,
        }

    def _find_subject_keywords(self, subject):
        """
        Find issue-related keywords in the subject.
        """

        return [
            keyword
            for keyword in self.SUBJECT_KEYWORDS
            if keyword in subject
        ]

    def _is_structured_feedback(self, body):
        """
        Check whether the email follows our
        structured feedback format.
        """

        return all(
            field in body
            for field in self.FEEDBACK_FIELDS
        )