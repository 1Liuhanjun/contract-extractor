# 两阶段合同字段提取系统

## 架构

```
合同原文 → [Stage 1: LLM事实抽取] → 原始事实 → [Stage 2: LLM标准化映射] → Excel
                            ↑                          ↑
                    只问"合同写了什么"          结合知识库+少样本
                    输出{value, evidence}       映射为业务标准值
```

## 为什么分两阶段？

| 阶段 | 任务 | 输出 | 核心问题 |
|------|------|------|----------|
| Stage 1 | 阅读理解 | 事实 + 原文证据 | "合同里写了什么？" |
| Stage 2 | 业务映射 | 标准字段值 | "业务人员会填什么？" |

**关键区别**：合同写"收到发票之日起30日内"是Stage 1的事，而"业务系统里这个字段填45天"是Stage 2的事。两者不应混淆。

## 项目结构

```
contract_extractor_v2/
├── main.py                    # 入口
├── field_knowledge_base.py    # 字段知识库（定义、规则、样本）
├── stage1_fact_extraction.py  # Stage 1: 事实抽取 prompt
├── stage2_standardizer.py     # Stage 2: 标准化映射
├── few_shot_examples.py       # 少样本实例（人工参考答案）
├── llm_client.py              # LLM API 客户端
├── excel_writer.py            # Excel 输出
├── requirements.txt           # 依赖
└── README.md                  # 本文件
```

## 使用方式

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

```bash
# 方式一：环境变量
export ANTHROPIC_API_KEY='your-key-here'

# 方式二：在 llm_client.py 中直接填入
```

### 3. 运行提取

```bash
# 处理单个合同
python main.py --input 合同文本.txt --output 结果.xlsx

# 批量处理
python main.py --input "contracts/*.txt" --output 结果.xlsx

# 预览模式（不调API，只看prompt）
python main.py --input 合同文本.txt --preview

# 保存中间结果
python main.py --input 合同文本.txt --output 结果.xlsx --save-intermediate ./debug
```

### 4. 使用 DeepSeek 或 OpenAI

```bash
export DEEPSEEK_API_KEY='your-key-here'
python main.py --input 合同文本.txt --api deepseek
```

## 添加新的少样本实例

每完成一份合同的核对，就添加一个少样本实例：

```python
# few_shot_examples.py
add_example(
    contract_name="京东物流 × XX公司",
    facts={
        "甲方_全称": {"value": "京东物流供应链有限公司", "evidence": "..."},
        # ... 其他事实
    },
    output={
        "客户分类": "京东",
        "业务类型": "一干",
        # ... 其他标准值
    }
)
```

## 字段知识库说明

`field_knowledge_base.py` 中是所有字段的定义，每个字段包含：

- **description**: 定义
- **source**: 来源（contract/internal）
- **allowed_values**: 允许的值列表
- **mapping_rules**: 原文→标准值的映射规则
- **known_examples**: 已知的映射实例
- **note**: 注意事项

当业务规则变化时，只需要修改这个文件，不需要改代码逻辑。

## 注意事项

1. 部分字段（登记日期、快递单号、钉钉审批单号等）不在合同中，需从其他系统获取
2. 联系人/电话/地址字段存在"合同通知联系人"和"现场运营对接人"两套信息，需确认用哪套
3. 分类字段（客户分类/业务类型/合同类型）需要在知识库中维护映射规则
