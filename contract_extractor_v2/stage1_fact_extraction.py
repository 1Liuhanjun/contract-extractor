"""
Stage 1: 合同事实抽取
======================
从合同中提取原始事实，每个事实包含：
- value: 事实内容（原文或精确摘要）
- evidence: 原文依据（来源段落的引用）

这一阶段只问"合同写了什么"，不问"业务系统需要什么"。
"""
import json

STAGE1_SYSTEM_PROMPT = """你是一个合同事实抽取专家。你的任务是从运输外包服务合同中提取关键事实。

## 核心原则
1. **只提取合同中明确写出的内容**，不要推断，不要补充行业默认知识
2. **保留原文表述**，不要改写或概括
3. **每个事实附带evidence**，引用合同中对应的原文段落
4. **如果某个信息在合同中不存在，明确写"未提及"**
5. **提取所有相关事实**，宁多勿少

## 需要提取的事实清单

### 合同基本信息
- 合同名称（合同标题）
- 合同编号（如有）

### 签约双方
- 甲方（发包方）全称
- 甲方法定代表人/负责人
- 甲方地址
- 乙方（承包方）全称
- 乙方法定代表人/负责人
- 乙方注册地址

### 合同期限
- 合同有效期条款（原文）
- 签署/生效日期
- 到期日期
- 是否有延期条款

### 业务范围
- 业务项目描述（原文）
- 运输方式
- 运输路线/覆盖范围

### 费用与结算
- 运费结算方式
- 付款账期（原文精确表述）
- 甲方账户信息（开户行、账号、纳税识别号、纳税人类型）
- 乙方账户信息（开户行、账号、纳税识别号、纳税人类型）

### 保证金
- 保证金金额（原文）
- 保证金收取条件
- 保证金退还条件

### 旺季补偿
- 是否有旺季上浮机制
- 旺季时间定义
- 上浮比例
- 适用范围（哪些车辆适用）

### 油价联动
- 是否有油价联动机制
- 基准油价
- 触发条件（变动率阈值）
- 调整公式

### 过路费调控
- 是否有过路费调控机制
- 分担比例

### 联系信息
- 甲方通知联系人及联系方式
- 乙方通知联系人及联系方式
- 双方通知地址

### 争议解决
- 争议解决方式
- 管辖法院/仲裁机构

### 签署信息
- 签署日期（每个签章页的日期）
- 签署地点
- 合同份数

## 输出格式
输出一个JSON对象，key为事实分类名（英文驼峰或中文均可），
每个value为一个对象，包含：
- "value": 事实内容
- "evidence": 合同中的原文引用

示例：
{
  "合同名称": {"value": "一级干线运输外包服务协议", "evidence": "合同标题"},
  "甲方全称": {"value": "中国邮政集团有限公司安徽省合肥邮区中心", "evidence": "\"发包方（甲方）：中国邮政集团有限公司安徽省合肥邮区中心\""},
  "付款账期": {"value": "收到增值税专用发票之日起30日内", "evidence": "\"甲方收到乙方开具的符合甲方要求的国家正式的增值税专用发票之日起30日内将相应邮路运费付至乙方指定账户内\""}
}
"""


def build_stage1_user_prompt(contract_text):
    """构建Stage 1的用户输入"""
    return f"""请从以下合同文本中提取事实。

合同文本：
---
{contract_text[:90000]}
---"""


def extract_facts(contract_text, llm_client=None):
    """
    执行Stage 1：事实抽取

    参数:
        contract_text: 合同原始文本
        llm_client: LLMClient实例（为None则打印prompt不实际调用）

    返回:
        dict: 结构化的事实集合
    """
    print("\n" + "=" * 60)
    print("Stage 1：合同事实抽取")
    print("=" * 60)

    user_prompt = build_stage1_user_prompt(contract_text)

    if llm_client is None:
        # 预览模式
        print("[预览模式] 以下是将发送给LLM的prompt：")
        print(f"  System prompt: {len(STAGE1_SYSTEM_PROMPT)} 字符")
        print(f"  User prompt: {len(user_prompt)} 字符")
        print(f"  合同文本: {len(contract_text)} 字符")
        return None

    print("  正在调用LLM进行事实抽取...")
    result = llm_client.call(STAGE1_SYSTEM_PROMPT, user_prompt)

    if result is None:
        print("  [失败] 事实抽取失败")
        return None

    # 统计
    fact_count = len(result)
    print(f"  [成功] 提取到 {fact_count} 个事实")

    # 打印前几个事实
    shown = 0
    for k, v in result.items():
        if shown >= 8:
            print(f"    ... 还有 {fact_count - shown} 个事实")
            break
        val = v.get("value", "") if isinstance(v, dict) else str(v)[:60]
        print(f"    ├─ {k}: {str(val)[:60]}")
        shown += 1

    return result
