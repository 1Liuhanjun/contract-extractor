"""模板标红一致性：模板里**红色表头**的列，必须正好等于 field_map 里 extract:false
（系统不从合同提取、留空待人工/外部填）的列。防止"模板标红"与"系统实际不提取"漂移。
"""
import sys
import warnings
from pathlib import Path

import yaml
import openpyxl

CODE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE_DIR))

TEMPLATE = CODE_DIR / "templates" / "合同台账表_新(3).xlsx"


def _field_map():
    return yaml.safe_load((CODE_DIR / "field_map.yaml").read_text(encoding="utf-8"))


def _no_extract_cols(fm, section):
    """field_map 某段里 extract:false 的列字母集合（= 系统不提取、留空的红字列）。"""
    return {c["col"] for c in fm[section] if c.get("extract") is False}


def _red_header_cols(ws):
    """该 sheet 表头行里字体为红色(FFFF0000)的列字母集合。"""
    reds = set()
    for cell in ws[1]:
        if cell.value is None:
            continue
        col = cell.font.color
        rgb = getattr(col, "rgb", None) if col is not None else None
        if rgb is not None and str(rgb).upper().endswith("FF0000"):
            reds.add(cell.column_letter)
    return reds


def test_template_red_matches_no_extract():
    fm = _field_map()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # 忽略 openpyxl 对模板扩展的无害告警
        wb = openpyxl.load_workbook(TEMPLATE)
    ledger = next(w for w in wb.worksheets if w.title.startswith(fm["ledger_sheet_prefix"]))
    yz = next(w for w in wb.worksheets if w.title.startswith(fm["youzheng_sheet"]))

    assert _red_header_cols(ledger) == _no_extract_cols(fm, "ledger"), \
        "新合同台账：模板红字列 ≠ field_map extract:false 列"
    assert _red_header_cols(yz) == _no_extract_cols(fm, "youzheng"), \
        "邮政：模板红字列 ≠ field_map extract:false 列"
