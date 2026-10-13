from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    KeepTogether,
)
from xml.sax.saxutils import escape


class EmailPdfGenerator:
    """
    Generates a professional email-style PDF containing
    the original support email and extracted ticket details.
    """

    def generate(self, email, output_path):

        document = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            rightMargin=18 * mm,
            leftMargin=18 * mm,
            topMargin=16 * mm,
            bottomMargin=16 * mm,
            title=email.get("subject", "Support Email"),
            author=email.get("sender_name", ""),
        )

        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            "EmailTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            alignment=TA_LEFT,
            spaceAfter=8,
        )

        metadata_label = ParagraphStyle(
            "MetadataLabel",
            parent=styles["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=13,
        )

        metadata_value = ParagraphStyle(
            "MetadataValue",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=13,
        )

        section_style = ParagraphStyle(
            "Section",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            spaceBefore=10,
            spaceAfter=6,
        )

        body_style = ParagraphStyle(
            "EmailBody",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=14,
            spaceAfter=5,
        )

        field_label_style = ParagraphStyle(
            "FieldLabel",
            parent=styles["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=12,
        )

        field_value_style = ParagraphStyle(
            "FieldValue",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=12,
        )

        story = []

        # ---------------------------------------------------------
        # Header
        # ---------------------------------------------------------

        story.append(
            Paragraph(
                "Support Email",
                title_style,
            )
        )

        # ---------------------------------------------------------
        # Email metadata
        # ---------------------------------------------------------

        sender = escape(
            email.get("sender_name", "")
        )

        sender_email = escape(
            email.get("sender_email", "")
        )

        if sender and sender_email:
            sender_display = (
                f"{sender} &lt;{sender_email}&gt;"
            )
        else:
            sender_display = sender_email or sender

        metadata = [
            [
                Paragraph("From", metadata_label),
                Paragraph(sender_display, metadata_value),
            ],
            [
                Paragraph(
                    "Date",
                    metadata_label,
                ),
                Paragraph(
                    escape(
                        email.get(
                            "received_at",
                            "",
                        )
                    ),
                    metadata_value,
                ),
            ],
            [
                Paragraph(
                    "Subject",
                    metadata_label,
                ),
                Paragraph(
                    escape(
                        email.get(
                            "subject",
                            "",
                        )
                    ),
                    metadata_value,
                ),
            ],
        ]

        table = Table(
            metadata,
            colWidths=[
                25 * mm,
                145 * mm,
            ],
            hAlign="LEFT",
        )

        table.setStyle(
            TableStyle(
                [
                    (
                        "BACKGROUND",
                        (0, 0),
                        (0, -1),
                        colors.HexColor("#F3F4F6"),
                    ),
                    (
                        "VALIGN",
                        (0, 0),
                        (-1, -1),
                        "TOP",
                    ),
                    (
                        "BOX",
                        (0, 0),
                        (-1, -1),
                        0.5,
                        colors.HexColor("#D1D5DB"),
                    ),
                    (
                        "INNERGRID",
                        (0, 0),
                        (-1, -1),
                        0.25,
                        colors.HexColor("#E5E7EB"),
                    ),
                    (
                        "LEFTPADDING",
                        (0, 0),
                        (-1, -1),
                        7,
                    ),
                    (
                        "RIGHTPADDING",
                        (0, 0),
                        (-1, -1),
                        7,
                    ),
                    (
                        "TOPPADDING",
                        (0, 0),
                        (-1, -1),
                        6,
                    ),
                    (
                        "BOTTOMPADDING",
                        (0, 0),
                        (-1, -1),
                        6,
                    ),
                ]
            )
        )

        story.append(table)

        story.append(Spacer(1, 10))

        # ---------------------------------------------------------
        # Forwarded email information
        # ---------------------------------------------------------

        if email.get("is_forwarded"):

            story.append(
                Paragraph(
                    "Forwarded Message",
                    section_style,
                )
            )

            forwarded_data = []

            if email.get("original_sender"):
                forwarded_data.append(
                    [
                        Paragraph(
                            "Original From",
                            metadata_label,
                        ),
                        Paragraph(
                            escape(
                                email[
                                    "original_sender"
                                ]
                            ),
                            metadata_value,
                        ),
                    ]
                )

            if email.get("original_date"):
                forwarded_data.append(
                    [
                        Paragraph(
                            "Original Date",
                            metadata_label,
                        ),
                        Paragraph(
                            escape(
                                email[
                                    "original_date"
                                ]
                            ),
                            metadata_value,
                        ),
                    ]
                )

            if email.get("original_subject"):
                forwarded_data.append(
                    [
                        Paragraph(
                            "Original Subject",
                            metadata_label,
                        ),
                        Paragraph(
                            escape(
                                email[
                                    "original_subject"
                                ]
                            ),
                            metadata_value,
                        ),
                    ]
                )

            if forwarded_data:

                forwarded_table = Table(
                    forwarded_data,
                    colWidths=[
                        35 * mm,
                        135 * mm,
                    ],
                    hAlign="LEFT",
                )

                forwarded_table.setStyle(
                    TableStyle(
                        [
                            (
                                "BACKGROUND",
                                (0, 0),
                                (0, -1),
                                colors.HexColor("#F9FAFB"),
                            ),
                            (
                                "VALIGN",
                                (0, 0),
                                (-1, -1),
                                "TOP",
                            ),
                            (
                                "BOX",
                                (0, 0),
                                (-1, -1),
                                0.5,
                                colors.HexColor("#D1D5DB"),
                            ),
                            (
                                "INNERGRID",
                                (0, 0),
                                (-1, -1),
                                0.25,
                                colors.HexColor("#E5E7EB"),
                            ),
                            (
                                "LEFTPADDING",
                                (0, 0),
                                (-1, -1),
                                7,
                            ),
                            (
                                "RIGHTPADDING",
                                (0, 0),
                                (-1, -1),
                                7,
                            ),
                            (
                                "TOPPADDING",
                                (0, 0),
                                (-1, -1),
                                5,
                            ),
                            (
                                "BOTTOMPADDING",
                                (0, 0),
                                (-1, -1),
                                5,
                            ),
                        ]
                    )
                )

                story.append(
                    forwarded_table
                )

        # ---------------------------------------------------------
        # Message
        # ---------------------------------------------------------

        story.append(
            Paragraph(
                "Message",
                section_style,
            )
        )

        body = email.get("body", "").strip()

        if body:

            body_lines = body.splitlines()

            for line in body_lines:

                line = escape(
                    line.strip()
                )

                if line:
                    story.append(
                        Paragraph(
                            line,
                            body_style,
                        )
                    )
                else:
                    story.append(
                        Spacer(1, 4)
                    )

        else:

            story.append(
                Paragraph(
                    "No message body was provided.",
                    body_style,
                )
            )

        # ---------------------------------------------------------
        # Structured ticket information
        # ---------------------------------------------------------

        structured_fields = [
            ("Center", email.get("center")),
            ("Module", email.get("module")),
            ("Description", email.get("description")),
            ("Issue", email.get("issue")),
            ("Steps to Reproduce", email.get("steps")),
            ("Expected Result", email.get("expected_result")),
            ("Actual Result", email.get("actual_result")),
        ]

        available_fields = [
            (label, value)
            for label, value in structured_fields
            if value
        ]

        if available_fields:

            story.append(
                Paragraph(
                    "Ticket Details",
                    section_style,
                )
            )

            rows = []

            for label, value in available_fields:

                formatted_value = escape(
                    str(value)
                ).replace(
                    "\n",
                    "<br/>",
                )

                rows.append(
                    [
                        Paragraph(
                            escape(label),
                            field_label_style,
                        ),
                        Paragraph(
                            formatted_value,
                            field_value_style,
                        ),
                    ]
                )

            ticket_table = Table(
                rows,
                colWidths=[
                    40 * mm,
                    130 * mm,
                ],
                repeatRows=0,
                hAlign="LEFT",
            )

            ticket_table.setStyle(
                TableStyle(
                    [
                        (
                            "BACKGROUND",
                            (0, 0),
                            (0, -1),
                            colors.HexColor("#F3F4F6"),
                        ),
                        (
                            "VALIGN",
                            (0, 0),
                            (-1, -1),
                            "TOP",
                        ),
                        (
                            "BOX",
                            (0, 0),
                            (-1, -1),
                            0.5,
                            colors.HexColor("#D1D5DB"),
                        ),
                        (
                            "INNERGRID",
                            (0, 0),
                            (-1, -1),
                            0.25,
                            colors.HexColor("#E5E7EB"),
                        ),
                        (
                            "LEFTPADDING",
                            (0, 0),
                            (-1, -1),
                            7,
                        ),
                        (
                            "RIGHTPADDING",
                            (0, 0),
                            (-1, -1),
                            7,
                        ),
                        (
                            "TOPPADDING",
                            (0, 0),
                            (-1, -1),
                            6,
                        ),
                        (
                            "BOTTOMPADDING",
                            (0, 0),
                            (-1, -1),
                            6,
                        ),
                    ]
                )
            )

            story.append(ticket_table)

        # ---------------------------------------------------------
        # Attachment information
        # ---------------------------------------------------------

        attachments = email.get(
            "attachments",
            []
        ) or email.get(
            "attachment_names",
            []
        )

        if attachments:

            story.append(
                Paragraph(
                    "Attachments",
                    section_style,
                )
            )

            for attachment in attachments:

                if isinstance(attachment, dict):
                    filename = escape(
                        attachment.get(
                            "filename",
                            "Unnamed attachment",
                        )
                    )
                else:
                    filename = escape(str(attachment))

                story.append(
                    Paragraph(
                        f"• {filename}",
                        body_style,
                    )
                )

        # ---------------------------------------------------------
        # Footer
        # ---------------------------------------------------------

        story.append(
            Spacer(1, 14)
        )

        story.append(
            Paragraph(
                "Generated automatically from the incoming support email.",
                ParagraphStyle(
                    "Footer",
                    parent=body_style,
                    fontSize=7.5,
                    textColor=colors.HexColor("#6B7280"),
                ),
            )
        )

        document.build(story)