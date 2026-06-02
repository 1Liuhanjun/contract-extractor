"""
Stage 2: 标准化映射
===================
将Stage 1提取的原始事实，结合字段知识库和少样本实例，
映射到标准的Excel字段值。

这一阶段负责"业务人员看到了这个事实，会把它写成什么"。
"""
import json

# ============================================================
# Stage 2 System Prompt（模板）
# ============================================================
STAGE2_SYSTEM_PROMPT_TPL = """你是一个合同字段标准化专家。你的任务是将从合同中提取的原始事实，映射到业务系统使用的标准字段值。

## 核心原则
1. **忠实于事实**：不要创造合同中没有的信息
2. **遵循知识库**：严格按照字段知识库的规则进行映射
3. **参考已知样本**：优先参考已确认的映射实例
4. **不确定时留空**：如果无法根据规则或样本确定标准值，输出 null 或空字符串

## 知识库与规则

{knowledge_base_text}

## 已知映射实例（少样本）

{few_shot_text}

## 要输出的字段

{output_fields_text}

## 输出格式
输出一个JSON对象，key为字段名，value为标准值。
注意：字段名使用Excel列名（如"账期（天）"、"油价基准(元/升）"）
"""


def _build_knowledge_base_text():
    """构建知识库文本（用于注入prompt）"""
    from field_knowledge_base import FIELD_KNOWLEDGE_BASE, get_extractable_fields

    fields = get_extractable_fields()
    lines = []

    for key, info in fields.items():
        from field_knowledge_base import get_excel_header_for_key
        header = get_excel_header_for_key(key)

        lines.append(f"### {header}")
        lines.append(f"定义: {info.get('description', '')}")

        allowed = info.get("allowed_values")
        if allowed:
            lines.append(f"允许的值: {allowed}")

        rules = info.get("mapping_rules", [])
        if rules:
            lines.append("映射规则:")
            for rule in rules:
                lines.append(f"  - 条件: {rule.get('condition', '')} → 取值: {rule.get('rule', '')}")

        note = info.get("note")
        if note:
            lines.append(f"注意: {note}")

        lines.append("")

    return "\n".join(lines)


def _build_few_shot_text(examples):
    """构建少样本文本（用于注入prompt）"""
    if not examples:
        return "（暂无已知样本）"

    lines = []
    for i, ex in enumerate(examples, 1):
        lines.append(f"### 示例 {i}")
        lines.append(f"事实: {json.dumps(ex.get('facts', {}), ensure_ascii=False, indent=2)}")
        lines.append(f"标准输出: {json.dumps(ex.get('output', {}), ensure_ascii=False, indent=2)}")
        lines.append("")

    return "\n".join(lines)


def _build_output_fields_text():
    """构建输出字段描述"""
    from field_knowledge_base import FIELD_KNOWLEDGE_BASE, get_extractable_fields, get_excel_header_for_key

    fields = get_extractable_fields()
    lines = []
    for key in sorted(fields.keys()):
        header = get_excel_header_for_key(key)
        info = fields[key]
        allowed = info.get("allowed_values")
        if allowed:
            lines.append(f"- {header} (允许值: {allowed})")
        else:
            lines.append(f"- {header}")

    return "\n".join(lines)


def build_stage2_prompt(extracted_facts, few_shot_examples=None):
    """构建完整的Stage2 prompt"""
    knowledge_base_text = _build_knowledge_base_text()
    few_shot_text = _build_few_shot_text(few_shot_examples or [])
    output_fields_text = _build_output_fields_text()

    system_prompt = STAGE2_SYSTEM_PROMPT_TPL.format(
        knowledge_base_text=knowledge_base_text,
        few_shot_text=few_shot_text,
        output_fields_text=output_fields_text
    )

    user_prompt = f"""请根据以下从合同中提取的事实，输出标准化的字段值。

提取的事实：
{json.dumps(extracted_facts, ensure_ascii=False, indent=2)}"""

    return system_prompt, user_prompt


def standardize_fields(extracted_facts, few_shot_examples=None, llm_client=None):
    """
    执行Stage 2：标准化映射

    参数:
        extracted_facts: Stage 1输出的原始事实字典
        few_shot_examples: 少样本实例列表
        llm_client: LLMClient实例

    返回:
        dict: 标准化后的字段值
    """
    print("\n" + "=" * 60)
    print("Stage 2：标准化映射")
    print("=" * 60)

    system_prompt, user_prompt = build_stage2_prompt(
        extracted_facts, few_shot_examples
    )

    if llm_client is None:
        # 预览模式
        print("[预览模式] 以下是将发送给LLM的prompt：")
        print(f"  System prompt: {len(system_prompt)} 字符")
        print(f"  User prompt: {len(user_prompt)} 字符")
        return None

    print("  正在调用LLM进行标准化映射...")
    result = llm_client.call(system_prompt, user_prompt)

    if result is None:
        print("  [失败] 标准化映射失败")
        return None

    print(f"  [成功] 输出 {len(result)} 个字段")
    for k, v in result.items():
        if v is not None and v != "":
            print(f"    ├─ {k}: {str(v)[:50]}")

    return result


# ============================================================
# 离线路由（当没有LLM时，使用规则+样本直接映射）
# ============================================================
def offline_standardize(extracted_facts, few_shot_examples=None):
    """
    离线模式：不使用LLM，仅通过规则和样本做映射
    适用于已知字段的快速处理

    注意：这只处理contract来源的字段，internal字段返回空
    """
    from field_knowledge_base import FIELD_KNOWLEDGE_BASE, get_excel_header_for_key

    result = {}

    # 对每个字段尝试映射
    for key, info in FIELD_KNOWLEDGE_BASE.items():
        header = get_excel_header_for_key(key)

        if info.get("source") == "internal":
            result[header] = ""
            continue

        # 尝试在extracted_facts中找到对应的事实
        fact_key = info.get("stage1_fact_key", "")
        if not fact_key or fact_key not in extracted_facts:
            result[header] = ""
            continue

        fact_value = extracted_facts[fact_key]
        if isinstance(fact_value, dict):
            fact_value = fact_value.get("value", str(fact_value))

        fact_str = str(fact_value)

        # 检查已知样本
        for example in info.get("known_examples", []):
            ex_fact = example.get("fact", "")
            if ex_fact and ex_fact in fact_str:
                result[header] = example["value"]
                break
        else:
            # 没有匹配到样本，留空
            result[header] = ""

    return result
