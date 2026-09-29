from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
)
from reportlab.lib.units import mm
from xml.sax.saxutils import escape


class EmailPdfGenerator:
    """
    Generates a PDF containing the original email details.
    """

    def generate(self, email, output_path):
        document = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            rightMargin=20 * mm,
            leftMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
        )

        styles = getSampleStyleSheet()

        title_style = styles["Title"]
        normal_style = styles["BodyText"]

        story = []

        story.append(
            Paragraph(
                "Original Support Email",
                title_style,
            )
        )

        story.append(Spacer(1, 10))

        story.append(
            Paragraph(
                f"<b>From:</b> "
                f"{escape(email.get('sender_email', ''))}",
                normal_style,
            )
        )

        story.append(
            Paragraph(
                f"<b>Date:</b> "
                f"{escape(email.get('received_at', ''))}",
                normal_style,
            )
        )

        story.append(
            Paragraph(
                f"<b>Subject:</b> "
                f"{escape(email.get('subject', ''))}",
                normal_style,
            )
        )

        story.append(Spacer(1, 10))

        story.append(
            Paragraph(
                "<b>Message:</b>",
                normal_style,
            )
        )

        story.append(Spacer(1, 5))

        body = escape(email.get("body", ""))

        body = body.replace("\n", "<br/>")

        story.append(
            Paragraph(
                body,
                normal_style,
            )
        )

        document.build(story)