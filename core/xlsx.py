"""最小可用的 .xlsx 產生器（純標準函式庫：zipfile + 手寫 XML）。

專案硬限制為「除 requests 外只用標準庫」，故不引入 openpyxl / xlsxwriter；
.xlsx 本質是一包 ZIP + XML，這裡手寫最小 OOXML（SpreadsheetML），
以 inline string 省去共享字串表，足以被 Excel / LibreOffice / Numbers 開啟。
僅支援多工作表的純值表格（字串 / 數值 / 布林 / 空白），不含樣式、公式與合併儲存格。
"""
from __future__ import annotations

import re
import zipfile
from xml.sax.saxutils import escape

# XML 1.0 不允許的控制字元（保留 \t \n \r）；輸出原文可能夾帶，寫入前先濾除
_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _col_letter(n: int) -> str:
    """1-based 欄序轉 Excel 欄名：1→A、27→AA。"""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _text(s: str) -> str:
    return escape(_ILLEGAL_XML.sub("", s))


def _cell(ref: str, value) -> str:
    """單一儲存格 XML。None/空字串→留空；bool→TRUE/FALSE 字串；數值→數字格；其餘→inline 字串。"""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):                       # bool 是 int 子類，需先判斷
        return f'<c r="{ref}" t="inlineStr"><is><t>{"TRUE" if value else "FALSE"}</t></is></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"><v>{value}</v></c>'
    return (f'<c r="{ref}" t="inlineStr"><is>'
            f'<t xml:space="preserve">{_text(str(value))}</t></is></c>')


def _sheet_xml(rows) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<sheetData>']
    for ri, row in enumerate(rows, 1):
        cells = "".join(_cell(f"{_col_letter(ci)}{ri}", v) for ci, v in enumerate(row, 1))
        out.append(f'<row r="{ri}">{cells}</row>')
    out.append('</sheetData></worksheet>')
    return "".join(out)


def write_workbook(path: str, sheets) -> str:
    """把多個工作表寫成一個 .xlsx。

    sheets：list of (sheet_name, rows)，rows 為 list[list]（含表頭列）。回傳 path。
    工作表名稱上限 31 字、且不可含 : \\ / ? * [ ]（呼叫端自行確保）。
    """
    n = len(sheets)

    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, n + 1))
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + overrides +
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>')

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>')

    sheet_tags = "".join(
        f'<sheet name="{_text(name)}" sheetId="{i}" r:id="rId{i}"/>'
        for i, (name, _rows) in enumerate(sheets, 1))
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{sheet_tags}</sheets></workbook>')

    rels = "".join(
        f'<Relationship Id="rId{i}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, n + 1))
    rels += (f'<Relationship Id="rId{n + 1}" '
             'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
             'Target="styles.xml"/>')
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + rels + '</Relationships>')

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        '</styleSheet>')

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook_xml)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        z.writestr("xl/styles.xml", styles_xml)
        for i, (_name, rows) in enumerate(sheets, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(rows))
    return path
