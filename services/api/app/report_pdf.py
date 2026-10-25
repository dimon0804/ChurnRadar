"""PDF еженедельного отчёта на reportlab: чистый Python, без браузера и системных библиотек.

Кириллице нужен TrueType-шрифт. В образе api лежит DejaVu Sans (/app/fonts, копируется из
пакета fonts-dejavu-core на этапе сборки); при запуске из репозитория ищем его в системе.
Шрифта нет — PDF всё равно соберётся встроенной Helvetica, но без кириллицы.
"""

from __future__ import annotations

import io
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.reports import LEVEL_TITLES, done_lines, kpis, money, num

FONT_DIRS = [
    os.getenv("REPORT_FONT_DIR", ""),
    "/app/fonts",
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/usr/local/share/fonts",
]
WINDOWS_FONTS = [("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf")]

RED = colors.HexColor("#dc2626")
BLUE = colors.HexColor("#2563eb")
GREY = colors.HexColor("#6b7280")
LINE = colors.HexColor("#e5e7eb")


@lru_cache(maxsize=1)
def fonts() -> tuple[str, str]:
    """Имена зарегистрированных шрифтов (обычный, жирный)."""
    pairs = [
        (str(Path(d) / "DejaVuSans.ttf"), str(Path(d) / "DejaVuSans-Bold.ttf")) for d in FONT_DIRS if d
    ] + WINDOWS_FONTS
    for regular, bold in pairs:
        if Path(regular).is_file():
            pdfmetrics.registerFont(TTFont("Report", regular))
            pdfmetrics.registerFont(TTFont("Report-Bold", bold if Path(bold).is_file() else regular))
            return "Report", "Report-Bold"
    return "Helvetica", "Helvetica-Bold"


def _styles() -> dict[str, ParagraphStyle]:
    regular, bold = fonts()
    return {
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=18, leading=22, spaceAfter=2),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=12.5, leading=16, spaceBefore=10, spaceAfter=5),
        "p": ParagraphStyle("p", fontName=regular, fontSize=9.5, leading=13),
        "muted": ParagraphStyle("muted", fontName=regular, fontSize=8.5, leading=11, textColor=GREY),
        "kt": ParagraphStyle("kt", fontName=regular, fontSize=8, leading=10, textColor=GREY),
        "kv": ParagraphStyle("kv", fontName=bold, fontSize=15, leading=18),
        "kh": ParagraphStyle("kh", fontName=regular, fontSize=7.5, leading=9.5),
        "cell": ParagraphStyle("cell", fontName=regular, fontSize=8.5, leading=10.5),
    }


def _esc(text: Any) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _line_chart(values: list[int], title: str, color: colors.Color, width: float, labels: list[str]) -> Drawing:
    regular, _ = fonts()
    d = Drawing(width, 150)
    d.add(String(0, 138, title, fontName=regular, fontSize=9, fillColor=GREY))
    plot = LinePlot()
    plot.x, plot.y, plot.width, plot.height = 48, 22, width - 60, 104
    plot.data = [list(enumerate(v / 100 for v in values))]
    plot.lines[0].strokeColor = color
    plot.lines[0].strokeWidth = 2
    plot.xValueAxis.valueMin, plot.xValueAxis.valueMax = 0, max(len(values) - 1, 1)
    step = max(1, len(values) // 6)
    plot.xValueAxis.valueSteps = list(range(0, len(values), step))
    plot.xValueAxis.labelTextFormat = lambda i: labels[int(i)][5:] if 0 <= int(i) < len(labels) else ""
    plot.yValueAxis.valueMin = 0
    plot.yValueAxis.labelTextFormat = lambda v: "€" + f"{v:,.0f}".replace(",", " ")
    for axis in (plot.xValueAxis, plot.yValueAxis):
        axis.labels.fontName, axis.labels.fontSize = regular, 7
        axis.strokeColor = LINE
    plot.yValueAxis.gridStrokeColor = LINE
    plot.yValueAxis.visibleGrid = True
    d.add(plot)
    return d


def _reasons_chart(reasons: list[dict[str, Any]], width: float) -> Drawing:
    regular, _ = fonts()
    rows = list(reversed(reasons))
    height = 30 + 22 * len(rows)
    d = Drawing(width, height)
    chart = HorizontalBarChart()
    chart.x, chart.y, chart.width, chart.height = 170, 10, width - 230, height - 20
    chart.data = [[r["expected_loss"] / 100 for r in rows]]
    chart.categoryAxis.categoryNames = [(r["title"] or "")[:34] for r in rows]
    chart.categoryAxis.labels.fontName, chart.categoryAxis.labels.fontSize = regular, 7.5
    chart.categoryAxis.labels.boxAnchor = "e"
    chart.categoryAxis.strokeColor = LINE
    chart.valueAxis.valueMin = 0
    chart.valueAxis.labels.fontName, chart.valueAxis.labels.fontSize = regular, 7
    chart.valueAxis.labelTextFormat = lambda v: "€" + f"{v:,.0f}".replace(",", " ")
    chart.valueAxis.strokeColor = LINE
    chart.bars[0].fillColor = RED
    chart.bars[0].strokeColor = None
    chart.barLabelFormat = lambda v: "€" + f"{v:,.0f}".replace(",", " ")
    chart.barLabels.fontName, chart.barLabels.fontSize = regular, 7
    chart.barLabels.boxAnchor = "w"
    chart.barLabels.dx = 3
    d.add(chart)
    return d


def _table(data: list[list[Any]], widths: list[float], num_cols: tuple[int, ...] = ()) -> Table:
    regular, bold = fonts()
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), regular),
        ("FONTNAME", (0, 0), (-1, 0), bold),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), GREY),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    style += [("ALIGN", (c, 0), (c, -1), "RIGHT") for c in num_cols]
    t.setStyle(TableStyle(style))
    return t


# логотип в светлой теме на прозрачном фоне, 1796x522 (2x от исходных 898x261)
LOGO_PATH = Path(__file__).with_name("assets") / "logo.png"
LOGO_HEIGHT = 13 * mm


def _logo() -> list[Any]:
    if not LOGO_PATH.is_file():
        return []
    logo = Image(str(LOGO_PATH), width=LOGO_HEIGHT * 1796 / 522, height=LOGO_HEIGHT)
    logo.hAlign = "LEFT"
    return [logo, Spacer(1, 4)]


def render_pdf(report: dict[str, Any]) -> bytes:
    s = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
        title="ChurnRadar — еженедельный отчёт", author="ChurnRadar", subject="Риски оттока за неделю",
    )
    width = doc.width
    story: list[Any] = [
        *_logo(),
        Paragraph("Еженедельный отчёт", s["h1"]),
        Paragraph(
            _esc(
                f"Период {report['period_from']} — {report['period_to']}. Пересчёт {report['scored_at'] or '—'}, "
                f"модель v{report['model_version'] or 0}. Суммы в евро."
            ),
            s["muted"],
        ),
        Spacer(1, 8),
    ]

    cards = [
        [Paragraph(_esc(k.title), s["kt"]), Paragraph(_esc(k.value), s["kv"]), Paragraph(_esc(k.hint), s["kh"])]
        for k in kpis(report)
    ]
    grid = [cards[i : i + 3] for i in range(0, len(cards), 3)]
    kpi_table = Table(
        [[Table([[p] for p in card], colWidths=[width / 3 - 10]) for card in row] for row in grid],
        colWidths=[width / 3] * 3,
    )
    kpi_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(kpi_table)

    history = report["history"]
    story.append(Paragraph("Динамика ожидаемой потери за 4 недели", s["h2"]))
    if len(history) >= 2:
        story.append(
            _line_chart([h["expected_loss"] for h in history], "по последнему пересчёту каждого дня", RED, width,
                        [h["day"] for h in history])
        )
    else:
        story.append(Paragraph("Истории пока мало: точка появляется после каждого пересчёта.", s["muted"]))

    forecast = report["forecast"]
    if len(forecast) >= 2:
        story.append(Paragraph(f"Прогноз потерь по дням на {report['horizon_days']} дней", s["h2"]))
        story.append(_line_chart([p["expected_loss"] for p in forecast], "ожидаемая потеря за день", BLUE, width,
                                 [p["date"] for p in forecast]))

    story.append(Paragraph("Главные причины", s["h2"]))
    if report["reasons"]:
        story.append(_reasons_chart(report["reasons"], width))
        story.append(Spacer(1, 8))
        story.append(_table(
            [["Причина", "Клиентов", "Потеря"]]
            + [[Paragraph(_esc(r["title"]), s["cell"]), num(r["customers"]), money(r["expected_loss"])]
               for r in report["reasons"]],
            [width * 0.6, width * 0.15, width * 0.25], (1, 2),
        ))
    else:
        story.append(Paragraph("Причин пока нет.", s["muted"]))

    rows = [["ID", "Почта", "Риск", "Score", "Потеря", "Главная причина"]] + [
        [f"#{c['id']}", Paragraph(_esc(c["email"]), s["cell"]), LEVEL_TITLES.get(c["risk_level"], c["risk_level"]),
         num(c["risk_score"], 1), money(c["expected_loss"]), Paragraph(_esc(c["reason"] or "—"), s["cell"])]
        for c in report["customers"]
    ]
    story.append(KeepTogether([
        Paragraph("Топ клиентов под риском", s["h2"]),
        _table(rows, [width * w for w in (0.09, 0.3, 0.1, 0.09, 0.14, 0.28)], (3, 4))
        if report["customers"] else Paragraph("Клиентов под риском нет.", s["muted"]),
    ]))

    story.append(Paragraph("Что сделали за неделю", s["h2"]))
    story += [Paragraph("• " + _esc(line), s["p"]) for line in done_lines(report)]
    story += [Spacer(1, 10), Paragraph(_esc(f"Сформировано {report['generated_at']}."), s["muted"])]

    doc.build(story)
    return buf.getvalue()
