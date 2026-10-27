import hashlib
import re
import unicodedata
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.pdfgen.canvas import Canvas

from backend.config.settings import PDF_DATE_SOURCE, PDF_MAX_EMBED_MB


_ACCENT = colors.HexColor("#2b5797")
_MUTED = colors.HexColor("#666666")

def _local_date(value):
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).astimezone().strftime(
            "%a, %d %b %Y, %H:%M %Z"
        )
    except (TypeError, ValueError, OverflowError):
        return str(value)


class _NumberedCanvas(Canvas):
    def __init__(self, *args, message_id="", **kwargs):
        super().__init__(*args, **kwargs)
        self.message_id = message_id
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        page_count = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.saveState()
            self.setFont("Helvetica", 8)
            self.setFillColor(_MUTED)
            self.drawString(
                18 * mm,
                10 * mm,
                f"Message {self.message_id[:8]} - generated {datetime.now().astimezone():%Y-%m-%d %H:%M}",
            )
            self.drawRightString(
                A4[0] - 18 * mm,
                10 * mm,
                f"Page {self._pageNumber} of {page_count}",
            )
            self.restoreState()
            Canvas.showPage(self)
        Canvas.save(self)


class EmailPdfGenerator:
    def filename(self, email, message_id, subject=None, center=None):
        existing = email.get("pdf_filename")
        if existing:
            return existing
        received = email.get("received_at", "")
        date_value = datetime.now().astimezone()
        if PDF_DATE_SOURCE == "received" and received:
            try:
                date_value = parsedate_to_datetime(received).astimezone()
            except (TypeError, ValueError, OverflowError):
                pass
        subject = subject or email.get("subject", "Support Email")
        center = center or email.get("center_or_enterprise") or ""
        digest = hashlib.sha1(message_id.encode("utf-8")).hexdigest()[:8]
        parts = [
            date_value.strftime("%Y-%m-%d"),
            date_value.strftime("%H%M"),
        ]
        if center:
            parts.append(self._safe_segment(center, 30))
        parts.extend((self._safe_segment(subject, 60), digest))
        filename = "_".join(part for part in parts if part) + ".pdf"
        if len(filename) > 120:
            extension = ".pdf"
            filename = filename[: 120 - len(extension)] + extension
        return filename

    @staticmethod
    def _safe_segment(value, maximum):
        value = unicodedata.normalize("NFKD", str(value))
        value = value.encode("ascii", "ignore").decode("ascii")
        value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "", value)
        value = re.sub(r"\s+", "_", value).strip(" ._")
        value = value[:maximum].strip(" ._")
        if value.upper() in {
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{number}" for number in range(1, 10)),
            *(f"LPT{number}" for number in range(1, 10)),
        }:
            value = f"_{value}"
        return value

    def generate(
        self,
        email,
        output_path,
        *,
        subject=None,
        ticket_type=None,
        center=None,
        assignee=None,
        attachment_statuses=None,
    ):
        output_path = Path(output_path)
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(
            name="TicketTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=17,
            leading=21,
            alignment=TA_LEFT,
            textColor=_ACCENT,
            spaceAfter=5,
        ))
        styles.add(ParagraphStyle(
            name="MetaLabel",
            parent=styles["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=11,
        ))
        styles.add(ParagraphStyle(
            name="MetaValue",
            parent=styles["BodyText"],
            fontSize=8,
            leading=11,
            splitLongWords=True,
        ))
        styles.add(ParagraphStyle(
            name="TicketBody",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#1a1a1a"),
            splitLongWords=True,
            spaceAfter=5,
        ))
        styles.add(ParagraphStyle(
            name="MutedSmall",
            parent=styles["BodyText"],
            fontSize=8,
            leading=11,
            textColor=_MUTED,
        ))
        styles.add(ParagraphStyle(
            name="MessageHeading",
            parent=styles["Heading3"],
            fontSize=9,
            leading=12,
            textColor=_ACCENT,
        ))

        final_subject = subject or email.get("subject", "Support request")
        document = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            rightMargin=18 * mm,
            leftMargin=18 * mm,
            topMargin=18 * mm,
            bottomMargin=18 * mm,
            title=final_subject,
            author=email.get("sender_name", ""),
        )
        message_id = str(email.get("message_id", ""))
        story = [
            Paragraph(escape(final_subject), styles["TicketTitle"]),
            Paragraph(
                escape(" | ".join(
                    str(value)
                    for value in (ticket_type, center, assignee)
                    if value
                )),
                styles["MutedSmall"],
            ),
            Spacer(1, 7),
        ]

        metadata = [
            ("From", self._sender_display(email)),
            ("To", email.get("to", "")),
            ("Cc", email.get("cc", "")),
            ("Date", _local_date(email.get("received_at", ""))),
            ("Original subject", email.get("subject", "")),
            ("Message ID", message_id[:16]),
        ]
        if email.get("reply_to_issue_id"):
            metadata.append(("Reply to ticket", f"#{email['reply_to_issue_id']}"))
        rows = [
            [
                Paragraph(escape(label), styles["MetaLabel"]),
                Paragraph(escape(str(value)), styles["MetaValue"]),
            ]
            for label, value in metadata
            if value
        ]
        if rows:
            table = Table(rows, colWidths=[30 * mm, 140 * mm])
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eeeeee")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.extend((table, Spacer(1, 8)))

        messages = email.get("thread_messages") or [{
            "role": "latest",
            "depth": 0,
            "subject": email.get("subject"),
            "from_": email.get("sender_name"),
            "date": email.get("received_at"),
            "paragraphs": [email.get("body", "")],
            "signature_lines": [],
        }]
        for index, message in enumerate(messages):
            title = "Latest message" if index == 0 else (
                "Original request" if index == len(messages) - 1 else "Earlier message"
            )
            paragraphs = [
                Paragraph(escape(str(paragraph)), styles["TicketBody"])
                for paragraph in message.get("paragraphs", [])
                if paragraph
            ]
            if not paragraphs:
                paragraphs = [Paragraph("No message text.", styles["MutedSmall"])]
            content = [
                Paragraph(escape(title), styles["MessageHeading"]),
                Paragraph(
                    escape(" - ".join(
                        str(value)
                        for value in (
                            message.get("from_"),
                            message.get("date"),
                            message.get("subject"),
                        )
                        if value
                    )),
                    styles["MutedSmall"],
                ),
                *paragraphs,
            ]
            for signature in message.get("signature_lines", []):
                content.append(Paragraph(escape(signature), styles["MutedSmall"]))
            card = Table(
                [[content]],
                colWidths=[document.width - min(message.get("depth", 0), 3) * 5 * mm],
            )
            card.setStyle(TableStyle([
                ("LINEBEFORE", (0, 0), (0, 0), 2, _ACCENT),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.append(KeepTogether([card, Spacer(1, 6)]))

        filtered_count = len(email.get("filtered_attachments", []))
        if filtered_count:
            story.append(Paragraph(
                f"{filtered_count} filtered images omitted",
                styles["MutedSmall"],
            ))
        if email.get("boilerplate_removed"):
            story.append(Paragraph(
                "Standard footer / confidentiality text omitted (see original email)",
                styles["MutedSmall"],
            ))

        max_embed_bytes = PDF_MAX_EMBED_MB * 1024 * 1024
        for attachment in email.get("attachments", []):
            path = Path(attachment.get("path", ""))
            if (
                path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".bmp"}
                or not path.is_file()
                or path.stat().st_size > max_embed_bytes
            ):
                continue
            try:
                image = Image(str(path))
                max_width = document.width
                max_height = 120 * mm
                scale = min(max_width / image.imageWidth, max_height / image.imageHeight, 1)
                image.drawWidth = image.imageWidth * scale
                image.drawHeight = image.imageHeight * scale
                story.extend((
                    image,
                    Paragraph(escape(path.name), styles["MutedSmall"]),
                    Spacer(1, 5),
                ))
            except (OSError, ValueError):
                continue

        statuses = attachment_statuses or []
        if statuses:
            story.append(Paragraph("Attachments", styles["MessageHeading"]))
            status_rows = [[
                Paragraph("Name", styles["MetaLabel"]),
                Paragraph("Type", styles["MetaLabel"]),
                Paragraph("Size", styles["MetaLabel"]),
                Paragraph("Status", styles["MetaLabel"]),
            ]]
            for attachment in statuses:
                status_rows.append([
                    Paragraph(escape(str(attachment.get("filename", ""))), styles["MetaValue"]),
                    Paragraph(escape(str(attachment.get("content_type", ""))), styles["MetaValue"]),
                    Paragraph(escape(str(attachment.get("size", ""))), styles["MetaValue"]),
                    Paragraph(escape(str(attachment.get("status", ""))), styles["MetaValue"]),
                ])
            story.append(Table(status_rows, colWidths=[70 * mm, 40 * mm, 25 * mm, 35 * mm]))
        document.build(
            story,
            canvasmaker=lambda *args, **kwargs: _NumberedCanvas(
                *args,
                message_id=message_id,
                **kwargs,
            ),
        )

    @staticmethod
    def _sender_display(email):
        name = email.get("sender_name") or ""
        address = email.get("sender_email") or ""
        return f"{name} ({address})" if name and address else address or name
