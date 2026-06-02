"""
Stage 2: 标准化映射 — 2026-06-02 业务确认版
===============================================
将 Stage 1 的事实映射为标准字段值。

关键规则：
  - 账期 = 结算周期 + 交付周期
  - 合同时间：中间段落优先
  - 合同类型：中标人决定
  - 合同名称由代码生成（第一条线路名+一干运输合同）
  - 合同编码由代码生成（BHWL-YZ邮政-YYYYMMDDNNN）
"""
import json


STAGE2_SYSTEM_PROMPT_TPL = """你是一个合同字段标准化专家。根据合同事实、知识库规则和少样本实例，输出标准字段值。

## 核心原则
1. 忠实于事实，不创造合同中没有的信息
2. 遵循知识库规则
3. 不信时留空

## 知识库与规则

{knowledge_base_text}

## 已知映射实例

{few_shot_text}

## 要输出的字段

{output_fields_text}

## 输出格式
JSON对象，key为字段名，value为标准值。注意字段名使用Excel列名。
"""


def _build_knowledge_base_text():
    from field_knowledge_base import FIELD_KNOWLEDGE_BASE, get_extractable_fields, get_excel_header_for_key

    fields = get_extractable_fields()
    lines = []

    for key, info in fields.items():
        header = get_excel_header_for_key(key)
        lines.append(f"### {header}")
        lines.append(f"定义: {info.get('description', '')}")

        allowed = info.get("allowed_values")
        if allowed:
            lines.append(f"允许值: {allowed}")

        rules = info.get("mapping_rules", [])
        if rules:
            lines.append("映射规则:")
            for r in rules:
                lines.append(f"  - {r.get('condition','')} → {r.get('rule','')}")

        note = info.get("note")
        if note:
            lines.append(f"注意: {note}")
        lines.append("")

    return "\n".join(lines)


def _build_few_shot_text(examples):
    if not examples:
        return "（暂无）"

    lines = []
    for i, ex in enumerate(examples, 1):
        lines.append(f"### 示例 {i}")
        lines.append(f"事实: {json.dumps(ex.get('facts', {}), ensure_ascii=False, indent=2)}")
        lines.append(f"输出: {json.dumps(ex.get('output', {}), ensure_ascii=False, indent=2)}")
        lines.append("")
    return "\n".join(lines)


def _build_output_fields_text():
    from field_knowledge_base import FIELD_KNOWLEDGE_BASE, get_extractable_fields, get_excel_header_for_key

    fields = get_extractable_fields()
    lines = []
    for key in sorted(fields.keys()):
        header = get_excel_header_for_key(key)
        info = fields[key]
        allowed = info.get("allowed_values")
        if allowed:
            lines.append(f"- {header} (允许: {allowed})")
        else:
            lines.append(f"- {header}")
    return "\n".join(lines)


def build_stage2_prompt(extracted_facts, few_shot_examples=None):
    knowledge_base_text = _build_knowledge_base_text()
    few_shot_text = _build_few_shot_text(few_shot_examples or [])
    output_fields_text = _build_output_fields_text()

    system_prompt = STAGE2_SYSTEM_PROMPT_TPL.format(
        knowledge_base_text=knowledge_base_text,
        few_shot_text=few_shot_text,
        output_fields_text=output_fields_text
    )

    user_prompt = f"""根据以下合同事实，输出标准化字段值。

提取的事实：
{json.dumps(extracted_facts, ensure_ascii=False, indent=2)}"""

    return system_prompt, user_prompt


def standardize_fields(extracted_facts, few_shot_examples=None, llm_client=None):
    print("\n" + "=" * 60)
    print("Stage 2：标准化映射")
    print("=" * 60)

    system_prompt, user_prompt = build_stage2_prompt(extracted_facts, few_shot_examples)

    if llm_client is None:
        print("[预览模式]")
        print(f"  System: {len(system_prompt)} 字符")
        print(f"  User: {len(user_prompt)} 字符")
        return None

    print("  正在调用LLM...")
    result = llm_client.call(system_prompt, user_prompt, max_tokens=4096)

    if result is None:
        print("  [失败] 标准化映射失败")
        return None

    print(f"  [成功] {len(result)} 个字段")
    for k, v in result.items():
        if v and v != "" and v != "null":
            print(f"    ├─ {k}: {str(v)[:50]}")

    return result


def offline_standardize(extracted_facts, few_shot_examples=None):
    from field_knowledge_base import FIELD_KNOWLEDGE_BASE, get_excel_header_for_key, is_red_field

    result = {}
    for key, info in FIELD_KNOWLEDGE_BASE.items():
        header = get_excel_header_for_key(key)
        if info.get("source") == "internal" or is_red_field(key):
            result[header] = ""
            continue

        fact_key = info.get("stage1_fact_key", "")
        if not fact_key or fact_key not in extracted_facts:
            result[header] = ""
            continue

        fact_value = extracted_facts[fact_key]
        if isinstance(fact_value, dict):
            fact_value = fact_value.get("value", str(fact_value))

        for example in info.get("known_examples", []):
            ex_fact = example.get("fact", "")
            if ex_fact and ex_fact in str(fact_value):
                result[header] = example["value"]
                break
        else:
            result[header] = ""

    return result
