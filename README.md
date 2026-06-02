# 合同字段智能提取系统

两阶段 LLM 架构，从 OCR 扫描件中自动提取物流运输合同的关键字段。

## 快速启动

```bash
cd src && python3 webapp.py
# 浏览器打开 http://localhost:8080
```

## 项目结构

```
合同提取项目/
├── src/                  # 核心代码
│   ├── webapp.py         # Web 入口
│   ├── stage1_fact_extraction.py   # Stage 1: 事实抽取
│   ├── stage2_standardizer.py      # Stage 2: 标准化映射
│   ├── field_knowledge_base.py     # 字段知识库
│   ├── few_shot_examples.py        # 少样本实例
│   ├── llm_client.py               # LLM API 客户端
│   └── excel_writer.py             # Excel 输出
├── web/                  # 前端
│   ├── templates/
│   └── static/
├── scripts/              # 工具脚本
│   ├── main.py           # 命令行入口
│   └── test_llm.py       # API 连通测试
├── docs/                 # 文档
│   ├── CLAUDE.md
│   └── 字段提取业务规则手册.md
└── data/                 # 运行时数据
    ├── uploads/
    ├── reviews/
    └── results/
```

## 依赖

```bash
pip install openpyxl python-dotenv
```

## 配置

编辑 `.env` 文件填入 API Key。
