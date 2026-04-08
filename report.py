"""
report.py  –  SentiVision PDF Report Generator
Professional, multi-page analytical report built with ReportLab + Matplotlib.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Optional

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, inch
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Flowable,
    Paragraph, Spacer, Table, TableStyle,
    Image, PageBreak, HRFlowable, NextPageTemplate,
)
from reportlab.pdfgen import canvas as rl_canvas

# ── Track-ID → 3-letter alias (mirrors app.py logic) ──────────────────────────
_ID_LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ'   # 23 chars, no I/O/W

def _track_id_to_alias(tid: int) -> str:
    base = len(_ID_LETTERS)
    i    = max(0, tid - 1)
    return (
        _ID_LETTERS[(i // (base * base)) % base] +
        _ID_LETTERS[(i // base) % base] +
        _ID_LETTERS[i % base]
    )

# ─────────────────────────────────────────────────────────────────────────────
# Brand palette
# ─────────────────────────────────────────────────────────────────────────────
C_GREEN_DARK  = colors.HexColor('#1a6b3c')
C_GREEN       = colors.HexColor('#27ae60')
C_GREEN_LIGHT = colors.HexColor('#d4edda')
C_GREEN_PALE  = colors.HexColor('#f0faf4')
C_ACCENT      = colors.HexColor('#f0b429')
C_DARK        = colors.HexColor('#0f1e17')
C_SLATE       = colors.HexColor('#2d3748')
C_MUTED       = colors.HexColor('#718096')
C_BORDER      = colors.HexColor('#e2e8f0')
C_WHITE       = colors.white
C_POSITIVE    = colors.HexColor('#22c55e')
C_NEGATIVE    = colors.HexColor('#ef4444')
C_ROW_ALT     = colors.HexColor('#f8fffe')

EMOTION_HEX = {
    'Happy':   '#f59e0b',
    'Surprise':'#f97316',
    'Sad':     '#3b82f6',
    'Fear':    '#8b5cf6',
    'Angry':   '#ef4444',
    'Disgust': '#10b981',
}

PW, PH        = A4
MARGIN_OUTER  = 18 * mm
MARGIN_INNER  = 18 * mm
BODY_W        = PW - MARGIN_OUTER - MARGIN_INNER
COL_GAP       = 6 * mm

# ─────────────────────────────────────────────────────────────────────────────
# Matplotlib global style
# ─────────────────────────────────────────────────────────────────────────────
plt.rcParams.update({
    'font.family':        'DejaVu Sans',
    'axes.spines.top':    False,
    'axes.spines.right':  False,
    'axes.spines.left':   False,
    'axes.spines.bottom': False,
    'axes.grid':          True,
    'grid.color':         '#e2e8f0',
    'grid.linewidth':     0.8,
    'axes.facecolor':     'white',
    'figure.facecolor':   'white',
    'text.color':         '#2d3748',
    'axes.labelcolor':    '#718096',
    'xtick.color':        '#718096',
    'ytick.color':        '#718096',
})


# ─────────────────────────────────────────────────────────────────────────────
# Datetime parser
# ─────────────────────────────────────────────────────────────────────────────
def _parse_dt(value: str) -> datetime:
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse datetime: {value!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Page header / footer callback
# ─────────────────────────────────────────────────────────────────────────────
class _PageDrawer:
    GENERATED = datetime.now().strftime('%B %d, %Y  \u2022  %I:%M %p')

    @staticmethod
    def _header_stripe(c: rl_canvas.Canvas) -> None:
        c.setFillColor(C_GREEN)
        c.rect(0, PH - 6 * mm, PW, 6 * mm, stroke=0, fill=1)

    @staticmethod
    def _footer(c: rl_canvas.Canvas, page_num: int) -> None:
        y = 10 * mm
        c.setFont('Helvetica-Bold', 7)
        c.setFillColor(C_GREEN)
        c.drawString(MARGIN_OUTER, y, 'SENTIVISION')
        c.setFont('Helvetica', 7)
        c.setFillColor(C_MUTED)
        c.drawString(MARGIN_OUTER + 22 * mm, y, '  Emotion Analysis Report')
        c.circle(MARGIN_OUTER + 55 * mm, y + 1.5, 1, fill=1, stroke=0)
        c.drawString(MARGIN_OUTER + 58 * mm, y, _PageDrawer.GENERATED)
        c.setFont('Helvetica-Bold', 7)
        c.setFillColor(C_SLATE)
        c.drawRightString(PW - MARGIN_OUTER, y, f'Page {page_num}')
        c.setStrokeColor(C_BORDER)
        c.setLineWidth(0.5)
        c.line(MARGIN_OUTER, y + 4 * mm, PW - MARGIN_OUTER, y + 4 * mm)

    def __call__(self, c: rl_canvas.Canvas, doc) -> None:
        c.saveState()
        self._header_stripe(c)
        self._footer(c, doc.page)
        c.restoreState()


# ─────────────────────────────────────────────────────────────────────────────
# Cover page – raw canvas Flowable
# ─────────────────────────────────────────────────────────────────────────────
def _draw_cover(c: rl_canvas.Canvas, report_data: dict) -> None:
    session  = report_data['session']
    stats    = report_data.get('overall_stats') or {}
    start_dt = _parse_dt(session['start_time'])
    end_dt   = _parse_dt(session['end_time']) if session.get('end_time') else datetime.now()
    dur_min  = int((end_dt - start_dt).total_seconds() / 60)

    # Dark forest panel (top 60%)
    panel_h = PH * 0.60
    c.setFillColor(C_DARK)
    c.rect(0, PH - panel_h, PW, panel_h, stroke=0, fill=1)

    # Left green accent stripe
    c.setFillColor(C_GREEN)
    c.rect(0, PH - panel_h, 5 * mm, panel_h, stroke=0, fill=1)

    # Diagonal accent (subtle)
    c.saveState()
    c.setFillColor(colors.Color(1, 1, 1, alpha=0.025))
    p = c.beginPath()
    p.moveTo(PW * 0.55, PH)
    p.lineTo(PW, PH)
    p.lineTo(PW, PH - panel_h)
    p.lineTo(PW * 0.78, PH - panel_h)
    p.close()
    c.drawPath(p, fill=1, stroke=0)
    c.restoreState()

    # Bottom light panel
    c.setFillColor(colors.HexColor('#f9fafb'))
    c.rect(0, 0, PW, PH - panel_h, stroke=0, fill=1)
    c.setFillColor(C_GREEN_LIGHT)
    c.rect(0, PH - panel_h - 1.5 * mm, PW, 1.5 * mm, stroke=0, fill=1)

    # ── Wordmark ──────────────────────────────────────────────────────────────
    logo_y = PH - 28 * mm
    c.setFont('Helvetica-Bold', 30)
    c.setFillColor(C_WHITE)
    c.drawString(18 * mm, logo_y, 'SENTI')
    c.setFillColor(C_GREEN)
    c.drawString(18 * mm + 75, logo_y, 'VISION')
    c.setFont('Helvetica', 8.5)
    c.setFillColor(colors.HexColor('#a0aec0'))
    c.drawString(18 * mm, logo_y - 6 * mm,
                 'Real-Time Customer Emotion & Sentiment Analysis System')

    # ── Main title ────────────────────────────────────────────────────────────
    c.setFont('Helvetica-Bold', 36)
    c.setFillColor(C_WHITE)
    c.drawString(18 * mm, PH - 70 * mm, 'Emotion Analysis')
    c.setFont('Helvetica', 36)
    c.drawString(18 * mm, PH - 82 * mm, 'Report')
    c.setStrokeColor(C_ACCENT)
    c.setLineWidth(3)
    c.line(18 * mm, PH - 86 * mm, 82 * mm, PH - 86 * mm)

    # ── Session metadata (dark panel) ─────────────────────────────────────────
    meta_y = PH - 108 * mm
    meta_items = [
        ('Session ID',  f'#{session["id"]}'),
        ('Date',        start_dt.strftime('%B %d, %Y')),
        ('Time',        f'{start_dt.strftime("%I:%M %p")}  \u2013  {end_dt.strftime("%I:%M %p")}'),
        ('Duration',    f'{dur_min} minutes'),
        ('Status',      session.get('status', 'completed').title()),
    ]
    for label, val in meta_items:
        c.setFont('Helvetica', 7.5)
        c.setFillColor(colors.HexColor('#a0aec0'))
        c.drawString(18 * mm, meta_y, label.upper())
        c.setFont('Helvetica-Bold', 9)
        c.setFillColor(C_WHITE)
        c.drawString(18 * mm + 27 * mm, meta_y, val)
        meta_y -= 7 * mm

    # ── KPI cards (bottom panel) ──────────────────────────────────────────────
    cards = [
        ('Total Detections', str(stats.get('total_detections', '\u2013'))),
        ('Frames Analyzed',  str(session.get('total_frames', '\u2013'))),
        ('Dominant Emotion', str(stats.get('dominant_emotion', '\u2013'))),
        ('Positive',         f"{stats.get('positive_percentage', 0):.1f}%"),
        ('Negative',         f"{stats.get('negative_percentage', 0):.1f}%"),
    ]
    bg_colors  = [C_GREEN_PALE] * 3 + [colors.HexColor('#f0fdf4'), colors.HexColor('#fff5f5')]
    val_colors = [C_GREEN_DARK] * 3 + [C_POSITIVE, C_NEGATIVE]

    n   = len(cards)
    gap = 4 * mm
    cw  = (PW - 2 * MARGIN_OUTER - gap * (n - 1)) / n
    cx  = MARGIN_OUTER
    cy  = 28 * mm
    ch  = 28 * mm

    for (label, val), bg, vc in zip(cards, bg_colors, val_colors):
        c.saveState()
        c.setFillColor(colors.HexColor('#00000012'))
        c.roundRect(cx + 1, cy - 1, cw, ch, 4, stroke=0, fill=1)
        c.restoreState()
        c.setFillColor(bg)
        c.setStrokeColor(C_BORDER)
        c.setLineWidth(0.5)
        c.roundRect(cx, cy, cw, ch, 4, stroke=1, fill=1)
        c.setFillColor(C_GREEN)
        c.roundRect(cx, cy + ch - 2, cw, 2, 1, stroke=0, fill=1)
        fs = 13 if len(val) <= 5 else 10
        c.setFont('Helvetica-Bold', fs)
        c.setFillColor(vc)
        c.drawCentredString(cx + cw / 2, cy + 10 * mm, val)
        c.setFont('Helvetica', 6.5)
        c.setFillColor(C_MUTED)
        c.drawCentredString(cx + cw / 2, cy + 5 * mm, label.upper())
        cx += cw + gap

    # ── Institution ───────────────────────────────────────────────────────────
    c.setFont('Helvetica', 7.5)
    c.setFillColor(C_MUTED)
    c.drawCentredString(PW / 2, 14 * mm,
        'Laguna State Polytechnic University  \u2022  Sta. Cruz Campus  \u2022  Philippines')
    c.setFont('Helvetica-Bold', 7.5)
    c.setFillColor(C_GREEN)
    c.drawCentredString(PW / 2, 10 * mm,
        'Vanesse Reyes  \u2022  Cel Rick D. Almario  \u2022  Keayon Ivan V. Romero')


class _CoverFlowable(Flowable):
    def __init__(self, report_data: dict):
        super().__init__()
        self._data = report_data
        self.width  = PW
        self.height = PH

    def wrap(self, aw, ah):
        return (PW, PH)

    def draw(self):
        _draw_cover(self.canv, self._data)


# ─────────────────────────────────────────────────────────────────────────────
# Chart builders
# ─────────────────────────────────────────────────────────────────────────────
def _buf_to_img(buf: io.BytesIO, w: float, h: float) -> Image:
    buf.seek(0)
    return Image(buf, width=w, height=h)


def _chart_sentiment_donut(stats: dict) -> io.BytesIO:
    pos = stats.get('positive_percentage', 0)
    neg = stats.get('negative_percentage', 0)
    fig, ax = plt.subplots(figsize=(4.2, 3.2))
    wedges, _, autotexts = ax.pie(
        [pos, neg],
        labels=['Positive', 'Negative'],
        colors=['#22c55e', '#ef4444'],
        autopct='%1.1f%%',
        startangle=90,
        pctdistance=0.72,
        wedgeprops=dict(width=0.52, edgecolor='white', linewidth=2),
        textprops={'fontsize': 9, 'color': '#2d3748'},
    )
    for at in autotexts:
        at.set_fontsize(9)
        at.set_fontweight('bold')
        at.set_color('white')
    ax.text(0,  0.08, 'Sentiment', ha='center', fontsize=8, color='#718096')
    ax.text(0, -0.22, 'Index',     ha='center', fontsize=8, color='#718096')
    ax.set_title('Overall Sentiment Distribution', fontsize=10,
                 fontweight='bold', color='#2d3748', pad=12)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=180, bbox_inches='tight', facecolor='white')
    plt.close()
    return buf


def _chart_emotion_bars(top_emotions: list) -> io.BytesIO:
    emotions    = [e['emotion'] for e in top_emotions[:6]]
    counts      = [e['count']   for e in top_emotions[:6]]
    bar_colors  = [EMOTION_HEX.get(e, '#27ae60') for e in emotions]
    fig, ax = plt.subplots(figsize=(6.5, 3.4))
    bars = ax.bar(emotions, counts, color=bar_colors, width=0.55, zorder=3, linewidth=0)
    for bar, col in zip(bars, bar_colors):
        ax.scatter(bar.get_x() + bar.get_width() / 2,
                   bar.get_height(), color=col, s=20, zorder=4)
    for bar in bars:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2,
                h + max(counts) * 0.015,
                f'{int(h):,}', ha='center', va='bottom',
                fontsize=8, fontweight='bold', color='#2d3748')
    ax.set_ylabel('Detection Count', fontsize=8.5, labelpad=6)
    ax.set_title('Emotion Frequency Breakdown', fontsize=10,
                 fontweight='bold', color='#2d3748', pad=10)
    ax.set_ylim(0, max(counts) * 1.18 if counts else 1)
    ax.yaxis.grid(True, linewidth=0.7, color='#e2e8f0')
    ax.set_axisbelow(True)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=180, bbox_inches='tight', facecolor='white')
    plt.close()
    return buf


def _chart_per_person(per_person: list) -> Optional[io.BytesIO]:
    if not per_person:
        return None
    labels   = [_track_id_to_alias(p["person_number"]) for p in per_person]
    pos_vals = [p['positive_percentage'] for p in per_person]
    neg_vals = [p['negative_percentage'] for p in per_person]
    x = np.arange(len(labels))
    w = 0.38
    fig, ax = plt.subplots(figsize=(max(6.5, len(labels) * 0.75), 3.4))
    ax.bar(x - w / 2, pos_vals, w, color='#22c55e', label='Positive', zorder=3, linewidth=0)
    ax.bar(x + w / 2, neg_vals, w, color='#ef4444', label='Negative', zorder=3, linewidth=0)
    for xi, (p, n) in enumerate(zip(pos_vals, neg_vals)):
        ax.text(xi - w / 2, p + 0.8, f'{p:.0f}%', ha='center',
                fontsize=7, fontweight='bold', color='#16a34a')
        ax.text(xi + w / 2, n + 0.8, f'{n:.0f}%', ha='center',
                fontsize=7, fontweight='bold', color='#dc2626')
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel('Percentage (%)', fontsize=8.5)
    ax.set_ylim(0, 115)
    ax.set_title('Per-Customer Sentiment Breakdown', fontsize=10,
                 fontweight='bold', color='#2d3748', pad=10)
    ax.yaxis.grid(True, linewidth=0.7, color='#e2e8f0')
    ax.set_axisbelow(True)
    ax.legend(fontsize=8, frameon=False, loc='upper right')
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=180, bbox_inches='tight', facecolor='white')
    plt.close()
    return buf


# ─────────────────────────────────────────────────────────────────────────────
# Layout helpers
# ─────────────────────────────────────────────────────────────────────────────
def _section_heading(text: str, S: dict) -> list:
    return [
        HRFlowable(width='100%', thickness=0.5, color=C_BORDER, spaceAfter=4),
        Paragraph(text, S['SecHead']),
        Spacer(1, 3 * mm),
    ]


def _kpi_table(rows: list, S: dict) -> Table:
    data = [
        [Paragraph(f'<b>{lbl}</b>', S['KpiLabel']),
         Paragraph(val, S['KpiValue'])]
        for lbl, val in rows
    ]
    t = Table(data, colWidths=[55 * mm, 80 * mm])
    t.setStyle(TableStyle([
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [C_GREEN_PALE, C_WHITE]),
        ('LEFTPADDING',   (0, 0), (-1, -1), 8),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
        ('TOPPADDING',    (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('GRID',          (0, 0), (-1, -1), 0.4, C_BORDER),
        ('LINEBELOW',     (0, -1), (-1, -1), 1, C_GREEN),
    ]))
    return t


# ─────────────────────────────────────────────────────────────────────────────
# Report Generator
# ─────────────────────────────────────────────────────────────────────────────
class ReportGenerator:

    def __init__(self):
        self.S = self._build_styles()

    @staticmethod
    def _build_styles() -> dict:
        S = {}
        S['SecHead'] = ParagraphStyle(
            'SecHead', fontName='Helvetica-Bold', fontSize=12,
            textColor=C_GREEN_DARK, spaceBefore=6, spaceAfter=2, leading=16)
        S['Body'] = ParagraphStyle(
            'Body', fontName='Helvetica', fontSize=9.5,
            textColor=C_SLATE, leading=14, alignment=TA_JUSTIFY, spaceAfter=6)
        S['BodySmall'] = ParagraphStyle(
            'BodySmall', fontName='Helvetica', fontSize=8.5,
            textColor=C_MUTED, leading=12, spaceAfter=4)
        S['KpiLabel'] = ParagraphStyle(
            'KpiLabel', fontName='Helvetica-Bold', fontSize=8.5, textColor=C_GREEN_DARK)
        S['KpiValue'] = ParagraphStyle(
            'KpiValue', fontName='Helvetica', fontSize=9, textColor=C_SLATE)
        S['TableHeader'] = ParagraphStyle(
            'TableHeader', fontName='Helvetica-Bold', fontSize=8.5,
            textColor=C_WHITE, alignment=TA_CENTER)
        S['TableCell'] = ParagraphStyle(
            'TableCell', fontName='Helvetica', fontSize=8.5,
            textColor=C_SLATE, alignment=TA_CENTER)
        S['Caption'] = ParagraphStyle(
            'Caption', fontName='Helvetica', fontSize=7.5,
            textColor=C_MUTED, alignment=TA_CENTER, spaceAfter=6)
        S['Bullet'] = ParagraphStyle(
            'Bullet', fontName='Helvetica', fontSize=9.5,
            textColor=C_SLATE, leading=14, leftIndent=10, spaceAfter=4)
        S['RecTitle'] = ParagraphStyle(
            'RecTitle', fontName='Helvetica-Bold', fontSize=9.5,
            textColor=C_GREEN_DARK, spaceAfter=2)
        return S

    # ── Analysis paragraphs ───────────────────────────────────────────────────
    def _analysis_paragraphs(self, stats: dict, top_emotions: list,
                              session: dict) -> list:
        S        = self.S
        start_dt = _parse_dt(session['start_time'])
        end_dt   = _parse_dt(session['end_time']) if session.get('end_time') else datetime.now()
        dur_min  = (end_dt - start_dt).total_seconds() / 60
        dominant = stats.get('dominant_emotion', 'N/A')
        pos_pct  = stats.get('positive_percentage', 0)
        neg_pct  = stats.get('negative_percentage', 0)
        total    = stats.get('total_detections', 0)

        interp = {
            'Happy':   'positive satisfaction and genuine enjoyment',
            'Sad':     'dissatisfaction or disappointment with the experience',
            'Angry':   'frustration, displeasure, or unmet expectations',
            'Fear':    'anxiety, discomfort, or perceived uncertainty',
            'Surprise':'unexpected reactions, which may be either positive or negative',
            'Disgust': 'strong disapproval or dissatisfaction with service or environment',
        }

        paras = [
            Paragraph(
                f'This analysis session was conducted on '
                f'<b>{start_dt.strftime("%B %d, %Y")}</b>, '
                f'from <b>{start_dt.strftime("%I:%M %p")}</b> to '
                f'<b>{end_dt.strftime("%I:%M %p")}</b>, spanning '
                f'<b>{dur_min:.0f} minutes</b> of continuous monitoring. '
                f'The system processed <b>{session.get("total_frames", 0):,}</b> video frames '
                f'and recorded <b>{total:,}</b> emotion detection events.',
                S['Body']
            ),
            Paragraph(
                f'The dominant emotion was <b>{dominant}</b>, indicating that customers '
                f'predominantly exhibited {interp.get(dominant, "various reactions")}. '
                f'This state accounted for '
                f'<b>{stats.get("total_" + dominant.lower(), 0):,}</b> detections.',
                S['Body']
            ),
        ]

        if pos_pct > 60:
            sent = (f'Sentiment is encouraging: <b>{pos_pct:.1f}%</b> positive versus '
                    f'<b>{neg_pct:.1f}%</b> negative, reflecting a broadly satisfying experience.')
        elif neg_pct > 60:
            sent = (f'Sentiment raises concern: <b>{neg_pct:.1f}%</b> of emotions were '
                    f'negative, versus only <b>{pos_pct:.1f}%</b> positive. Immediate '
                    f'investigation is warranted.')
        else:
            sent = (f'Sentiment is balanced: <b>{pos_pct:.1f}%</b> positive and '
                    f'<b>{neg_pct:.1f}%</b> negative, indicating a mixed experience '
                    f'with clear room for improvement.')
        paras.append(Paragraph(sent, S['Body']))

        if len(top_emotions) >= 3:
            t = top_emotions
            paras.append(Paragraph(
                f'Top three emotions: <b>{t[0]["emotion"]}</b> ({t[0]["count"]:,}), '
                f'<b>{t[1]["emotion"]}</b> ({t[1]["count"]:,}), and '
                f'<b>{t[2]["emotion"]}</b> ({t[2]["count"]:,}).',
                S['Body']
            ))
        return paras

    # ── Recommendations ───────────────────────────────────────────────────────
    def _recommendations(self, stats: dict) -> list:
        S       = self.S
        pos_pct = stats.get('positive_percentage', 0)
        neg_pct = stats.get('negative_percentage', 0)

        if pos_pct > 60:
            groups = [
                ('Sustain High Performance',
                 'Document the service practices that drove positive sentiment and use them '
                 'as benchmarks for staff training and future shift standards.'),
                ('Leverage Customer Advocacy',
                 'High positivity is the ideal time to collect testimonials and reviews. '
                 'Implement a structured post-visit feedback mechanism.'),
                ('Monitor Outlier Negatives',
                 'Identify the specific time windows where negative spikes occurred and '
                 'cross-reference with service logs to prevent recurrence.'),
            ]
        elif neg_pct > 50:
            groups = [
                ('Conduct Immediate Root-Cause Analysis',
                 'Review footage from peak-negative periods alongside service logs, wait '
                 'times, and order records to isolate systemic pain points.'),
                ('Prioritise Staff Training',
                 'Reinforce customer service training with emphasis on de-escalation, '
                 'proactive communication, and early detection of dissatisfaction.'),
                ('Establish a Service Recovery Protocol',
                 'Define clear staff procedures for intervening when negative emotions '
                 'are observed, including escalation paths and compensation guidelines.'),
                ('Increase Monitoring Frequency',
                 'Run analysis sessions more frequently during peak hours to track '
                 'whether corrective actions are producing measurable improvement.'),
            ]
        else:
            groups = [
                ('Target Friction Points',
                 'Analyse per-minute trends to isolate windows where negative emotions '
                 'cluster, then investigate staffing levels and environmental factors.'),
                ('Improve Consistency',
                 'Identify what drove positive states in this session and replicate those '
                 'conditions consistently across all shifts and service stages.'),
                ('Map the Customer Journey',
                 'Correlate emotion data with dining phases (arrival, ordering, dining, '
                 'billing) to pinpoint the touchpoints most influencing overall sentiment.'),
            ]

        items = []
        for title, body in groups:
            items.append(Paragraph(f'<b>{title}</b>', S['RecTitle']))
            items.append(Paragraph(f'\u2022  {body}', S['Bullet']))
            items.append(Spacer(1, 2 * mm))
        return items

    # ── Per-person table ──────────────────────────────────────────────────────
    def _per_person_table(self, per_person: list) -> Table:
        S = self.S
        header = [Paragraph(h, S['TableHeader']) for h in
                  ['ID', 'Stay Duration', 'Arrival', 'Departure', 'Dominant', 'Happy', 'Surprise', 'Sad',
                   'Fear', 'Angry', 'Disgust', 'Positive', 'Negative']]
        rows = [header]

        for i, p in enumerate(per_person):
            ec  = p.get('emotion_counts', {})
            dom = p.get('dominant_emotion', '\u2013')
            dc  = colors.HexColor(EMOTION_HEX.get(dom, '#27ae60'))
            dur = p.get('duration', '\u2013')
            alias = _track_id_to_alias(p['person_number'])
            
            # Format timestamps to time-only for the table to ensure they fit nicely
            def format_table_time(ts_str):
                if not ts_str or ts_str == '\u2013': return ts_str
                try:
                    dt = _parse_dt(ts_str)
                    return dt.strftime('%I:%M:%S %p')
                except:
                    return ts_str

            row = [
                Paragraph(alias, S['TableCell']),
                Paragraph(dur, S['TableCell']),
                Paragraph(format_table_time(p.get('first_seen', '\u2013')), S['TableCell']),
                Paragraph(format_table_time(p.get('last_seen', '\u2013')), S['TableCell']),
                Paragraph(f'<b>{dom}</b>',
                          ParagraphStyle('_DC', parent=S['TableCell'],
                                         textColor=dc, fontName='Helvetica-Bold')),
                Paragraph(str(ec.get('Happy', 0)),    S['TableCell']),
                Paragraph(str(ec.get('Surprise', 0)), S['TableCell']),
                Paragraph(str(ec.get('Sad', 0)),      S['TableCell']),
                Paragraph(str(ec.get('Fear', 0)),     S['TableCell']),
                Paragraph(str(ec.get('Angry', 0)),    S['TableCell']),
                Paragraph(str(ec.get('Disgust', 0)),  S['TableCell']),
                Paragraph(f'<b>{p["positive_percentage"]:.1f}%</b>',
                          ParagraphStyle('_PC', parent=S['TableCell'],
                                         textColor=C_POSITIVE, fontName='Helvetica-Bold')),
                Paragraph(f'<b>{p["negative_percentage"]:.1f}%</b>',
                          ParagraphStyle('_NC', parent=S['TableCell'],
                                         textColor=C_NEGATIVE, fontName='Helvetica-Bold')),
            ]
            rows.append(row)

        # Adjusted widths to accommodate more columns: Total ~174mm (BODY_W)
        col_w = [8*mm, 18*mm, 18*mm, 18*mm, 18*mm] + [10.5*mm]*6 + [12.5*mm, 12.5*mm]
        t = Table(rows, colWidths=col_w, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, 0), C_GREEN_DARK),
            ('TOPPADDING',    (0, 0), (-1, 0), 7),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 7),
            ('ROWBACKGROUNDS',(0, 1), (-1, -1), [C_ROW_ALT, C_WHITE]),
            ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING',    (0, 1), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
            ('GRID',          (0, 0), (-1, -1), 0.4, C_BORDER),
            ('LINEBELOW',     (0, 0), (-1, 0), 1.5, C_GREEN),
            ('LINEBELOW',     (0, -1), (-1, -1), 1, C_GREEN),
            ('BACKGROUND',    (11, 1), (11, -1), colors.HexColor('#f0fdf4')),
            ('BACKGROUND',    (12, 1), (12, -1), colors.HexColor('#fff5f5')),
        ]))
        return t

    # ── Emotion frequency table ───────────────────────────────────────────────
    def _emotion_freq_table(self, top_emotions: list, total: int) -> Table:
        S       = self.S
        pos_set = {'Happy', 'Surprise'}
        header  = [Paragraph(h, S['TableHeader']) for h in
                   ['Rank', 'Emotion', 'Detections', 'Share', 'Sentiment']]
        rows    = [header]
        medals  = ['1st', '2nd', '3rd', '4th', '5th', '6th']

        for i, e in enumerate(top_emotions):
            pct  = (e['count'] / total * 100) if total else 0
            sent = 'Positive' if e['emotion'] in pos_set else 'Negative'
            sc   = C_POSITIVE if sent == 'Positive' else C_NEGATIVE
            ec   = colors.HexColor(EMOTION_HEX.get(e['emotion'], '#27ae60'))
            rows.append([
                Paragraph(medals[i] if i < len(medals) else str(i + 1), S['TableCell']),
                Paragraph(f'<b>{e["emotion"]}</b>',
                          ParagraphStyle('_EC', parent=S['TableCell'],
                                         textColor=ec, fontName='Helvetica-Bold')),
                Paragraph(f'{e["count"]:,}', S['TableCell']),
                Paragraph(f'{pct:.1f}%',     S['TableCell']),
                Paragraph(f'<b>{sent}</b>',
                          ParagraphStyle('_SC', parent=S['TableCell'],
                                         textColor=sc, fontName='Helvetica-Bold')),
            ])

        t = Table(rows, colWidths=[18*mm, 35*mm, 30*mm, 25*mm, 30*mm], repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, 0), C_GREEN_DARK),
            ('TOPPADDING',    (0, 0), (-1, 0), 7),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 7),
            ('ROWBACKGROUNDS',(0, 1), (-1, -1), [C_ROW_ALT, C_WHITE]),
            ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING',    (0, 1), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
            ('GRID',          (0, 0), (-1, -1), 0.4, C_BORDER),
            ('LINEBELOW',     (0, 0), (-1, 0), 1.5, C_GREEN),
            ('LINEBELOW',     (0, -1), (-1, -1), 1, C_GREEN),
        ]))
        return t

    # ── Main entry point ──────────────────────────────────────────────────────
    def generate_report(self, report_data: dict, output_path: str) -> str:
        session      = report_data['session']
        stats        = report_data.get('overall_stats') or {}
        top_emotions = report_data.get('top_emotions') or []
        per_person   = report_data.get('per_person') or []
        S            = self.S

        page_drawer = _PageDrawer()
        body_frame  = Frame(
            MARGIN_OUTER, 18 * mm, BODY_W,
            PH - 18 * mm - 14 * mm,
            leftPadding=0, rightPadding=0,
            topPadding=8 * mm, bottomPadding=0,
        )
        cover_frame = Frame(0, 0, PW, PH, id='cover', leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        doc = BaseDocTemplate(
            output_path, pagesize=A4,
            leftMargin=MARGIN_OUTER, rightMargin=MARGIN_INNER,
            topMargin=14 * mm, bottomMargin=18 * mm,
        )
        doc.addPageTemplates([
            PageTemplate(id='cover', frames=[cover_frame]),
            PageTemplate(id='body', frames=[body_frame], onPage=page_drawer),
        ])

        story = []

        # ── Cover ─────────────────────────────────────────────────────────────
        story.append(_CoverFlowable(report_data))
        story.append(NextPageTemplate('body'))
        story.append(PageBreak())

        # ── Page 2: Executive Summary ─────────────────────────────────────────
        story.extend(_section_heading('Executive Summary', S))

        start_dt = _parse_dt(session['start_time'])
        end_dt   = _parse_dt(session['end_time']) if session.get('end_time') else datetime.now()
        dur_min  = int((end_dt - start_dt).total_seconds() / 60)

        story.append(_kpi_table([
            ('Session ID',         f'#{session["id"]}'),
            ('Analysis Date',      start_dt.strftime('%B %d, %Y')),
            ('Time Window',        f'{start_dt.strftime("%I:%M %p")} \u2013 {end_dt.strftime("%I:%M %p")}'),
            ('Duration',           f'{dur_min} minutes'),
            ('Frames Processed',   f'{session.get("total_frames", 0):,}'),
            ('Total Detections',   f'{stats.get("total_detections", 0):,}'),
            ('Dominant Emotion',   stats.get("dominant_emotion", "\u2013")),
            ('Positive Sentiment', f'{stats.get("positive_percentage", 0):.1f}%'),
            ('Negative Sentiment', f'{stats.get("negative_percentage", 0):.1f}%'),
        ], S))
        story.append(Spacer(1, 5 * mm))

        # Side-by-side: donut chart + analysis text
        donut_img = _buf_to_img(_chart_sentiment_donut(stats), 3.6 * inch, 2.7 * inch)
        analysis  = self._analysis_paragraphs(stats, top_emotions, session)
        col_right = BODY_W - 3.8 * inch - COL_GAP
        side = Table([[donut_img, analysis]],
                     colWidths=[3.8 * inch, col_right])
        side.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING',  (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING',   (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
        ]))
        story.append(side)
        story.append(PageBreak())

        # ── Page 3: Emotion Breakdown ─────────────────────────────────────────
        story.extend(_section_heading('Emotion Frequency Breakdown', S))
        story.append(_buf_to_img(_chart_emotion_bars(top_emotions), 5.6 * inch, 2.9 * inch))
        story.append(Paragraph(
            'Figure 1. Detection count per emotion across the full session.', S['Caption']))
        story.append(Spacer(1, 4 * mm))

        if top_emotions and stats.get('total_detections'):
            story.append(self._emotion_freq_table(top_emotions, stats['total_detections']))
            story.append(Spacer(1, 5 * mm))

        # ── Per-person section ────────────────────────────────────────────────
        if per_person:
            story.extend(_section_heading('Per-Customer Sentiment Analysis', S))
            pp_buf = _chart_per_person(per_person)
            if pp_buf:
                w = min(5.6 * inch, max(4 * inch, len(per_person) * 0.6 * inch))
                story.append(_buf_to_img(pp_buf, w, 2.9 * inch))
                story.append(Paragraph(
                    'Figure 2. Positive vs. Negative percentage per detected customer.',
                    S['Caption']))
                story.append(Spacer(1, 4 * mm))
            story.append(self._per_person_table(per_person))
            story.append(Spacer(1, 3 * mm))
            story.append(Paragraph(
                'Note: Customer numbers are tracker-assigned identifiers within this '
                'session only and do not represent persistent customer profiles.',
                S['BodySmall']))

        story.append(PageBreak())

        # ── Page 4: Recommendations & Conclusion ──────────────────────────────
        story.extend(_section_heading('Recommendations', S))
        story.extend(self._recommendations(stats))
        story.append(Spacer(1, 6 * mm))

        story.extend(_section_heading('Conclusion', S))
        story.append(Paragraph(
            f'This report delivers a comprehensive analysis of customer sentiment from '
            f'Session #{session["id"]} on {start_dt.strftime("%B %d, %Y")}. '
            f'With <b>{stats.get("total_detections", 0):,}</b> emotion events across '
            f'<b>{session.get("total_frames", 0):,}</b> frames, the data provides a '
            f'statistically meaningful foundation for understanding the customer experience. '
            f'The dominance of <b>{stats.get("dominant_emotion", "N/A")}</b> — combined '
            f'with a sentiment split of <b>{stats.get("positive_percentage", 0):.1f}%</b> '
            f'positive and <b>{stats.get("negative_percentage", 0):.1f}%</b> negative — '
            f'offers clear direction for targeted service improvements. '
            f'Continued use of SentiVision for regular monitoring will enable data-driven '
            f'decisions and measurable gains in customer satisfaction over time.',
            S['Body']
        ))

        story.append(Spacer(1, 8 * mm))
        story.append(HRFlowable(width='100%', thickness=0.5, color=C_BORDER))
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph('<b>About SentiVision</b>',
                                ParagraphStyle('_AH', parent=S['Body'],
                                               fontName='Helvetica-Bold',
                                               textColor=C_GREEN_DARK)))
        story.append(Paragraph(
            'SentiVision is a web-based system for real-time customer emotion and sentiment '
            'analysis using CCTV camera feeds, developed for restaurants in Pila, Laguna. '
            'It combines YOLOv8 person detection with deep facial emotion recognition to '
            'deliver actionable session-level analytics. '
            'Developed by <b>Vanesse Reyes</b>, <b>Cel Rick D. Almario</b>, and '
            '<b>Keayon Ivan V. Romero</b> at Laguna State Polytechnic University, '
            'Sta. Cruz Campus.',
            S['BodySmall']
        ))

        doc.build(story)
        return output_path