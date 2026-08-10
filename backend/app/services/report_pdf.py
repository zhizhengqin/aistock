"""F-03-06: render an AnalysisReport's report_json as a downloadable PDF.

Chinese text uses reportlab's built-in CID font STSong-Light (Adobe Asian
CMaps), so no TTF font file needs to be bundled or installed on the server.
"""
import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)
from reportlab.lib.styles import ParagraphStyle

_FONT = "STSong-Light"
_registered = False


def _ensure_font():
    global _registered
    if not _registered:
        pdfmetrics.registerFont(UnicodeCIDFont(_FONT))
        _registered = True


def _styles():
    _ensure_font()
    return {
        "title": ParagraphStyle("title", fontName=_FONT, fontSize=20, leading=26,
                                spaceAfter=4, textColor=HexColor("#1f2937")),
        "subtitle": ParagraphStyle("subtitle", fontName=_FONT, fontSize=10, leading=14,
                                   textColor=HexColor("#6b7280")),
        "h2": ParagraphStyle("h2", fontName=_FONT, fontSize=14, leading=20,
                             spaceBefore=14, spaceAfter=6, textColor=HexColor("#111827")),
        "body": ParagraphStyle("body", fontName=_FONT, fontSize=10.5, leading=16,
                               textColor=HexColor("#1f2937")),
        "small": ParagraphStyle("small", fontName=_FONT, fontSize=9, leading=13,
                                textColor=HexColor("#4b5563")),
        "disclaimer": ParagraphStyle("disclaimer", fontName=_FONT, fontSize=9, leading=13,
                                     textColor=HexColor("#9ca3af")),
    }


_ANALYST_NAMES = {
    "technical": "技术面分析师",
    "fundamental": "基本面分析师",
    "capital": "资金面分析师",
    "news": "新闻分析师",
    "sentiment": "情绪分析师",
    "risk": "风险分析师",
}

_DECISION_ROWS = [
    ("rating", "综合评级"), ("target_price", "目标价"), ("stop_loss", "止损价"),
    ("confidence", "置信度"), ("entry_range", "入场区间"), ("take_profit", "止盈目标"),
    ("holding_period", "持有期限"), ("position_size", "仓位建议"),
    ("risk_warning", "风险提示"),
]

_INFO_ROWS = [
    ("price", "最新价"), ("change_pct", "涨跌幅(%)"), ("pe_ttm", "市盈率TTM"),
    ("pb", "市净率"), ("market_cap", "总市值(亿)"), ("industry", "所属行业"),
]


def _fmt(value):
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (list, tuple)):
        return "、".join(str(v) for v in value) or "—"
    return str(value)


def _kv_table(rows, source, styles):
    data = [[Paragraph(label, styles["small"]),
             Paragraph(_fmt(source.get(key)), styles["body"])]
            for key, label in rows if source.get(key) not in (None, "", [])]
    if not data:
        return None
    t = Table(data, colWidths=[40 * mm, 120 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1),
         [HexColor("#f9fafb"), HexColor("#ffffff")]),
        ("LINEABOVE", (0, 0), (-1, 0), 0.5, HexColor("#e5e7eb")),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, HexColor("#e5e7eb")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def build_analysis_pdf(report: dict) -> bytes:
    """Build the full analysis report PDF; returns raw bytes."""
    styles = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"{report.get('stock_name', '')}投研报告",
    )
    story = []

    name = report.get("stock_name") or report.get("stock_code", "")
    code = report.get("stock_code", "")
    analyzed_at = str(report.get("analyzed_at", ""))[:19].replace("T", " ")

    story.append(Paragraph(f"睿见投研 · {name}（{code}）投研报告", styles["title"]))
    story.append(Paragraph(f"生成时间：{analyzed_at}　|　AI 辅助分析", styles["subtitle"]))
    story.append(HRFlowable(width="100%", thickness=1, color=HexColor("#e5e7eb"),
                            spaceBefore=8, spaceAfter=4))

    info = report.get("stock_info") or {}
    t = _kv_table(_INFO_ROWS, info, styles)
    if t:
        story.append(Paragraph("行情快照", styles["h2"]))
        story.append(t)

    decision = report.get("decision") or {}
    story.append(Paragraph("最终决策卡", styles["h2"]))
    t = _kv_table(_DECISION_ROWS, decision, styles)
    if t:
        story.append(t)
    if decision.get("meeting_summary"):
        story.append(Spacer(1, 4))
        story.append(Paragraph(f"投研会议总结：{decision['meeting_summary']}",
                               styles["body"]))
    if decision.get("key_watchpoints"):
        story.append(Paragraph("核心跟踪：" + "、".join(decision["key_watchpoints"]),
                               styles["small"]))

    analysts = report.get("analysts") or {}
    if analysts:
        story.append(Paragraph("分析师分报告", styles["h2"]))
        for key, data in analysts.items():
            if not isinstance(data, dict):
                continue
            title = _ANALYST_NAMES.get(key, key)
            score = _fmt(data.get("score"))
            suggestion = _fmt(data.get("suggestion"))
            story.append(Paragraph(f"{title}　评分 {score}　建议 {suggestion}",
                                   styles["body"]))
            summary = data.get("summary") or data.get("analysis") or ""
            if summary:
                story.append(Paragraph(str(summary), styles["small"]))
            story.append(Spacer(1, 4))

    story.append(Spacer(1, 16))
    story.append(HRFlowable(width="100%", thickness=0.5,
                            color=HexColor("#e5e7eb"), spaceAfter=6))
    disclaimer = report.get("disclaimer") or "本分析仅供参考，不构成任何投资建议。"
    story.append(Paragraph(
        f"免责声明：{disclaimer} 市场有风险，投资需谨慎。本报告由 AI 生成，"
        "睿见投研不提供代客理财服务，不承诺任何收益。", styles["disclaimer"]))

    doc.build(story)
    return buf.getvalue()
