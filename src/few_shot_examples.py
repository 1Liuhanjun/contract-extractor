"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

少样本实例 — 2026-06-02 清空重写
===================================
2026-06-02 按业务确认规则全面更新，旧少样本与新规则不兼容，全部清空。
新少样本将在新规则验证通过后逐步添加。
"""
from field_knowledge_base import FIELD_KNOWLEDGE_BASE, get_excel_header_for_key, is_red_field

FEW_SHOT_EXAMPLES = [
    # 待新规则验证后重写
]


def add_example(contract_name, facts, output, notes=None):
    example = {
        "contract_name": contract_name,
        "facts": facts,
        "output": output,
        "notes": notes or {}
    }
    FEW_SHOT_EXAMPLES.append(example)
    print(f"  [已添加] 少样本: {contract_name}")
    return example


def get_all_examples():
    return FEW_SHOT_EXAMPLES


def summarize_examples():
    summary = {}
    for field_key in FIELD_KNOWLEDGE_BASE:
        if is_red_field(field_key):
            continue
        header = get_excel_header_for_key(field_key)
        values = set()
        for ex in FEW_SHOT_EXAMPLES:
            val = ex.get("output", {}).get(header, "")
            if val:
                values.add(str(val))
        if values:
            summary[header] = sorted(values)
    return summary
