
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))

#!/usr/bin/env python3
"""
两阶段合同字段提取系统
=======================
Stage 1: LLM事实抽取 - 从原文提取 {value, evidence}
Stage 2: LLM标准化映射 - 结合知识库+少样本，映射为标准字段值

架构：
  合同原文 → [Stage 1] 事实 → [知识库+少样本] → [Stage 2] 标准值 → Excel

使用方式：
  1. 配置 API Key（环境变量或 config）
  2. python main.py --input 合同文本.txt --output 结果.xlsx
  3. 查看结果
"""
import argparse
import json
import os
import sys
import glob


def load_contract_text(file_path):
    """加载合同文本（支持 .txt 和 .docx）"""
    if not os.path.exists(file_path):
        print(f"  [错误] 文件不存在: {file_path}")
        return None

    if file_path.endswith('.docx'):
        try:
            import zipfile
            import xml.etree.ElementTree as ET
            with zipfile.ZipFile(file_path) as z:
                xml_content = z.read('word/document.xml')
                root = ET.fromstring(xml_content)
                texts = []
                for p in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'):
                    para_texts = []
                    for t in p.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'):
                        if t.text and not t.text.strip().startswith('<') and not t.text.strip().startswith('http'):
                            para_texts.append(t.text)
                    if para_texts:
                        texts.append(''.join(para_texts))
                return '\n'.join(texts)
        except Exception as e:
            print(f"  [警告] docx读取失败: {e}，尝试纯文本...")

    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        return f.read()


def run_single_contract(contract_text, llm_client, few_shot_examples,
                        save_intermediate=None, verbose=True):
    """处理单个合同：Stage 1 → Stage 2"""

    from stage1_fact_extraction import extract_facts
    from stage2_standardizer import standardize_fields

    # Stage 1：事实抽取
    print(f"\n{'='*60}")
    print(f"  Stage 1：事实抽取")
    print(f"{'='*60}")
    print(f"  合同文本: {len(contract_text)} 字符")

    facts = extract_facts(contract_text, llm_client)
    if facts is None:
        print("  [终止] 事实抽取失败")
        return None

    if save_intermediate:
        path = f"{save_intermediate}_stage1_facts.json"
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(facts, f, ensure_ascii=False, indent=2)
        print(f"  [已保存] 中间结果: {path}")

    # Stage 2：标准化映射
    print(f"\n{'='*60}")
    print(f"  Stage 2：标准化映射")
    print(f"{'='*60}")

    standardized = standardize_fields(facts, few_shot_examples, llm_client)
    if standardized is None:
        print("  [终止] 标准化映射失败")
        return None

    if save_intermediate:
        path = f"{save_intermediate}_stage2_standardized.json"
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(standardized, f, ensure_ascii=False, indent=2)
        print(f"  [已保存] 中间结果: {path}")

    return standardized


def print_summary(standardized, source_name=""):
    """打印提取结果摘要"""
    from field_knowledge_base import FIELD_KNOWLEDGE_BASE

    print(f"\n{'='*60}")
    print(f"  提取结果摘要 {source_name}")
    print(f"{'='*60}")

    core_fields = ["客户名称", "合同主体", "合同名称", "合同开始时间", "合同结束时间",
                   "账期（天）", "保证金（万元）", "客户分类", "业务类型", "合同类型",
                   "是否有旺季补偿", "是否有油价联动", "联系人", "电话"]

    for field in core_fields:
        val = standardized.get(field, "")
        if val and str(val).strip():
            print(f"  ✅ {field}: {val}")
        else:
            print(f"  ⬜ {field}: （空）")

    # 统计合同内字段的提取率
    from field_knowledge_base import get_excel_header_for_key
    contract_fields = [k for k, v in FIELD_KNOWLEDGE_BASE.items()
                       if v.get("source") == "contract"]
    filled = 0
    for k in contract_fields:
        from field_knowledge_base import get_excel_header_for_key
        h = get_excel_header_for_key(k)
        if h in standardized and standardized[h] not in (None, "", "null"):
            filled += 1

    print(f"\n  📊 合同内字段: {len(contract_fields)}个, 已提取: {filled}个")


def main():
    parser = argparse.ArgumentParser(
        description="两阶段合同字段提取系统 (Stage1 事实抽取 → Stage2 标准化映射)"
    )
    parser.add_argument("--input", "-i", required=True, help="输入文件路径（支持通配符）")
    parser.add_argument("--output", "-o", default="两阶段提取结果.xlsx", help="输出Excel路径")
    parser.add_argument("--api", "-a", choices=["anthropic", "deepseek", "openai"],
                        default="anthropic", help="LLM提供商")
    parser.add_argument("--model", help="模型名称（可选，默认使用各提供商的最新模型）")
    parser.add_argument("--save-intermediate", help="保存中间JSON结果的路径前缀")
    parser.add_argument("--preview", action="store_true", help="预览模式（不调用API，只显示prompt）")
    parser.add_argument("--offline", action="store_true", help="离线模式（不调用API，仅用规则匹配）")

    args = parser.parse_args()

    # 找出输入文件
    input_files = sorted(glob.glob(args.input))
    if not input_files:
        print(f"没有找到匹配的文件: {args.input}")
        sys.exit(1)
    print(f"找到 {len(input_files)} 个合同文件")

    # 初始化LLM客户端
    from llm_client import LLMClient
    llm_client = None
    if not args.preview and not args.offline:
        llm_client = LLMClient(provider=args.api, model=args.model)

    # 加载少样本实例
    from few_shot_examples import get_all_examples
    few_shot_examples = get_all_examples()
    print(f"已加载 {len(few_shot_examples)} 组少样本实例")

    # 逐文件处理
    all_results = []
    for file_path in input_files:
        print(f"\n{'#'*60}")
        print(f"# 处理: {os.path.basename(file_path)}")
        print(f"{'#'*60}")

        text = load_contract_text(file_path)
        if text is None or len(text) < 50:
            print(f"  [跳过] 文本过短或无法读取")
            continue

        print(f"  文本长度: {len(text)} 字符")

        if args.offline:
            # 离线模式（仅规则匹配，不调LLM）
            from stage1_fact_extraction import extract_facts
            from stage2_standardizer import offline_standardize
            facts = extract_facts(text, None)
            standardized = offline_standardize(
                facts or {}, few_shot_examples
            )
        else:
            # 正常两阶段流程
            save_prefix = None
            if args.save_intermediate:
                base = os.path.splitext(os.path.basename(file_path))[0]
                save_prefix = f"{args.save_intermediate}_{base}"

            standardized = run_single_contract(
                text, llm_client, few_shot_examples,
                save_intermediate=save_prefix,
                verbose=True
            )

        if standardized:
            all_results.append(standardized)
            print_summary(standardized, f"[{os.path.basename(file_path)}]")
        else:
            print(f"  [失败] {os.path.basename(file_path)} 提取失败")

    # 写入Excel
    if all_results:
        from excel_writer import write_results
        write_results(all_results, args.output)
        print(f"\n{'='*60}")
        print(f"全部完成！共处理 {len(input_files)} 个文件")
        print(f"成功提取: {len(all_results)} 份")
        print(f"输出文件: {args.output}")
        print(f"{'='*60}")
    else:
        print("\n[失败] 未能提取任何合同信息")


if __name__ == "__main__":
    main()
