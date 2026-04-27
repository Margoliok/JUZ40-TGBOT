from io import BytesIO
from pathlib import Path

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import MessageDelivery
from app.services import broadcast_stats, response_label


def _font_name() -> str:
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path.exists():
            name = "HRUnicode"
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(path)))
            return name
    return "Helvetica"


def _rows(deliveries: list[MessageDelivery]) -> list[dict[str, str]]:
    return [
        {
            "Қызметкер": delivery.employee.full_name,
            "Бөлім": delivery.employee.department,
            "Лауазым": delivery.employee.position,
            "Жауап": response_label(delivery.response),
            "Уақыты": delivery.response_at.strftime("%H:%M %d.%m.%Y") if delivery.response_at else "",
            "Сұрақ": delivery.question_text or "",
            "HR жауабы": delivery.hr_answer or "",
        }
        for delivery in deliveries
    ]


def export_excel(deliveries: list[MessageDelivery]) -> bytes:
    buffer = BytesIO()
    rows = _rows(deliveries)
    stats = broadcast_stats(deliveries)
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, index=False, sheet_name="Жауаптар")
        pd.DataFrame([stats]).to_excel(writer, index=False, sheet_name="Статистика")
    return buffer.getvalue()


def export_pdf(deliveries: list[MessageDelivery], title: str) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=24, leftMargin=24, topMargin=24, bottomMargin=24)
    styles = getSampleStyleSheet()
    font_name = _font_name()
    styles["Title"].fontName = font_name
    styles["Normal"].fontName = font_name
    story = [Paragraph(title, styles["Title"]), Spacer(1, 12)]

    stats = broadcast_stats(deliveries)
    stats_data = [
        ["Жіберілген", stats["total"]],
        ["Таныстым", stats["acknowledged"]],
        ["Келістім", stats["agreed"]],
        ["Сұрағым бар", stats["question"]],
        ["Жауап жоқ", stats["unanswered"]],
    ]
    story.append(Table(stats_data, style=[("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("FONTNAME", (0, 0), (-1, -1), font_name)]))
    story.append(Spacer(1, 16))

    data = [["Қызметкер", "Бөлім", "Лауазым", "Жауап", "Уақыты"]]
    for row in _rows(deliveries):
        data.append([row["Қызметкер"], row["Бөлім"], row["Лауазым"], row["Жауап"], row["Уақыты"]])
    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ]
        )
    )
    story.append(table)
    doc.build(story)
    return buffer.getvalue()
