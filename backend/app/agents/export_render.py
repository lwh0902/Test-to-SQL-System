"""Deterministic export renderers (no LLM).

Formats:
- report → PDF (minimal PDF) / DOCX (OOXML zip)
- evidence → CSV / XLSX (OOXML spreadsheet zip)

Stdlib-only to avoid new heavy deps; files are real openable office/PDF bytes.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Any, Iterable
from xml.sax.saxutils import escape


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def render_export_bytes(
    *,
    kind: str,
    fmt: str,
    report: dict | None,
    query: dict | None,
) -> bytes:
    kind = (kind or "report").lower()
    fmt = (fmt or "pdf").lower()
    report = report or {}
    query = query or {}

    if kind in ("evidence", "evidence_csv", "evidence_xlsx") or fmt in ("csv", "xlsx"):
        if fmt == "xlsx" or kind == "evidence_xlsx":
            return _render_xlsx(query)
        return _render_csv(query)

    # report narrative
    if fmt == "docx":
        return _render_docx(report)
    return _render_pdf(report)


def _rows_and_cols(query: dict) -> tuple[list[str], list[dict]]:
    rows = list(query.get("rows") or [])
    cols = list(query.get("columns") or [])
    if not cols and rows and isinstance(rows[0], dict):
        cols = list(rows[0].keys())
    return [str(c) for c in cols], rows


def _render_csv(query: dict) -> bytes:
    cols, rows = _rows_and_cols(query)
    buf = io.StringIO()
    writer = csv.writer(buf)
    if cols:
        writer.writerow(cols)
    for r in rows:
        if isinstance(r, dict):
            writer.writerow([r.get(c, "") for c in cols])
        elif isinstance(r, (list, tuple)):
            writer.writerow(list(r))
        else:
            writer.writerow([r])
    # utf-8-sig for Excel friendliness
    return buf.getvalue().encode("utf-8-sig")


def _col_name(idx: int) -> str:
    # 0 -> A, 25 -> Z, 26 -> AA
    n = idx + 1
    s = ""
    while n:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s


def _render_xlsx(query: dict) -> bytes:
    cols, rows = _rows_and_cols(query)
    # Build sheet XML
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]
    # header
    if cols:
        lines.append('<row r="1">')
        for i, c in enumerate(cols):
            ref = f"{_col_name(i)}1"
            lines.append(
                f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(c))}</t></is></c>'
            )
        lines.append("</row>")
    for r_i, row in enumerate(rows, start=2 if cols else 1):
        lines.append(f'<row r="{r_i}">')
        values: Iterable[Any]
        if isinstance(row, dict):
            values = [row.get(c, "") for c in cols] if cols else row.values()
        elif isinstance(row, (list, tuple)):
            values = row
        else:
            values = [row]
        for c_i, val in enumerate(values):
            ref = f"{_col_name(c_i)}{r_i}"
            text = escape(str(val if val is not None else ""))
            lines.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        lines.append("</row>")
    lines.append("</sheetData></worksheet>")
    sheet = "\n".join(lines)

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""
    wb = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="evidence" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
"""
    wb_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", wb)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def _report_paragraphs(report: dict) -> list[str]:
    paras = [f"深度诊断报告（生成于 {_utc_now_iso()}）", ""]
    for sec in report.get("sections") or []:
        title = str(sec.get("title") or "章节")
        content = str(sec.get("content") or "")
        eids = sec.get("evidence_ids") or []
        paras.append(title)
        paras.append(content)
        if eids:
            paras.append("证据: " + ", ".join(str(x) for x in eids))
        paras.append("")
    if not report.get("sections"):
        paras.append(json.dumps(report, ensure_ascii=False)[:2000])
    return paras


def _render_docx(report: dict) -> bytes:
    paras = _report_paragraphs(report)
    body_parts = []
    for p in paras:
        body_parts.append(
            f'<w:p><w:r><w:t xml:space="preserve">{escape(p)}</w:t></w:r></w:p>'
        )
    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {''.join(body_parts)}
    <w:sectPr/>
  </w:body>
</w:document>
"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document)
    return buf.getvalue()


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


_CJK_FONT_CANDIDATES = (
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
)


def _find_cjk_font_path() -> str | None:
    import os

    env = os.getenv("PDF_CJK_FONT") or os.getenv("DATAPILOT_PDF_FONT")
    if env and os.path.isfile(env):
        return env
    for p in _CJK_FONT_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def _render_pdf_reportlab(report: dict) -> bytes | None:
    """Prefer reportlab + system CJK TTF/TTC so Chinese is not '?'."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    except Exception:
        return None

    font_name = "Helvetica"
    font_path = _find_cjk_font_path()
    try:
        if font_path:
            # TTC may need subfontIndex; try plain register first
            try:
                pdfmetrics.registerFont(TTFont("DP_CJK", font_path))
                font_name = "DP_CJK"
            except Exception:
                # fallback CID font (Adobe built-in CJK in reportlab)
                pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
                font_name = "STSong-Light"
        else:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            font_name = "STSong-Light"
    except Exception:
        font_name = "Helvetica"

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    x, y = 48, height - 56
    c.setFont(font_name, 11)
    line_h = 16
    max_width = width - 96

    def draw_wrapped(text: str, size: int = 11, leading: int = 16):
        nonlocal y
        c.setFont(font_name, size)
        # simple char-wrap for CJK/latin mix
        line = ""
        for ch in text:
            trial = line + ch
            if c.stringWidth(trial, font_name, size) <= max_width:
                line = trial
            else:
                if y < 48:
                    c.showPage()
                    c.setFont(font_name, size)
                    y = height - 56
                c.drawString(x, y, line)
                y -= leading
                line = ch
        if line:
            if y < 48:
                c.showPage()
                c.setFont(font_name, size)
                y = height - 56
            c.drawString(x, y, line)
            y -= leading

    paras = _report_paragraphs(report) or ["（空报告）"]
    for i, p in enumerate(paras):
        # first line title-ish
        if i == 0:
            draw_wrapped(p, size=14, leading=20)
            y -= 6
        else:
            draw_wrapped(p, size=11, leading=16)
            y -= 4
    c.save()
    return buf.getvalue()


def _render_pdf_legacy_ascii(report: dict) -> bytes:
    """Last-resort stdlib PDF (non-ASCII → ?). Kept if reportlab unavailable."""
    lines: list[str] = []
    for p in _report_paragraphs(report):
        safe = []
        for ch in p:
            if ord(ch) < 128:
                safe.append(ch)
            else:
                safe.append("?")
        line = "".join(safe)
        while len(line) > 90:
            lines.append(line[:90])
            line = line[90:]
        lines.append(line)
    if not lines:
        lines = ["Empty report"]

    y = 800
    cmds = ["BT", "/F1 11 Tf", "14 TL", f"50 {y} Td"]
    first = True
    for line in lines[:60]:
        if not first:
            cmds.append("T*")
        first = False
        cmds.append(f"({_pdf_escape(line)}) Tj")
    cmds.append("ET")
    stream = "\n".join(cmds).encode("latin-1", errors="replace")

    objects: list[bytes] = []
    objects.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objects.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
    objects.append(
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n"
    )
    objects.append(
        f"4 0 obj<< /Length {len(stream)} >>stream\n".encode("latin-1")
        + stream
        + b"\nendstream\nendobj\n"
    )
    objects.append(b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects)+1}\n".encode("latin-1"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("latin-1"))
    out.extend(
        f"trailer<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode(
            "latin-1"
        )
    )
    return bytes(out)


def _render_pdf(report: dict) -> bytes:
    """PDF with CJK when reportlab + system font available; else ASCII fallback."""
    data = _render_pdf_reportlab(report)
    if data and data[:4] == b"%PDF":
        return data
    return _render_pdf_legacy_ascii(report)
