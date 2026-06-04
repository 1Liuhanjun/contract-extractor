# 合同主表与附表智能提取系统

本项目用于从物流运输合同中自动提取结构化信息，并输出 Excel。当前系统包含两条互相隔离的提取链路：

- **主表提取**：本项目原有的两阶段 LLM 流程，负责合同主表字段。
- **附表提取**：`Addition/` 子项目的原版附表流程，负责线路明细等附表字段。

PDF 会先调用 PaddleOCR 转成 Markdown；OCR 文本准备完成后，主表和附表会并行处理。最终 Excel 中会输出两个 sheet：`主表` 和 `附表`。

## 一、快速启动

建议使用 Python 3.10+。

```bash
cd 合同提取项目
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

复制环境变量模板：

```bash
cp .env.example .env
```

然后编辑 `.env`，填入自己的 API Key。不要把 `.env` 提交到 Git。

启动服务：

```bash
python3 src/webapp.py
```

浏览器打开：

```text
http://localhost:8080
```

## 二、必须配置的环境变量

`.env` 文件不会提交到仓库，所以别人部署时必须自己创建。最少需要：

```env
DEEPSEEK_API_KEY=你的DeepSeekKey
PADDLEOCR_ACCESS_TOKEN=你的PaddleOCRToken
PADDLEOCR_MODEL=PaddleOCR-VL-1.5
```

说明：

- `DEEPSEEK_API_KEY`：主表和附表的 LLM 调用都需要。
- `PADDLEOCR_ACCESS_TOKEN`：上传 PDF 时需要，用于把 PDF OCR 成 Markdown。
- `PADDLEOCR_MODEL`：当前主项目默认使用 `PaddleOCR-VL-1.5`。
- 如果只上传已经 OCR 好的 `.docx` 或 `.txt`，可以不触发 PaddleOCR；但实际生产建议直接传 PDF。

可选配置：

```env
PADDLEOCR_JOB_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
PADDLEOCR_POLL_INTERVAL=5
PADDLEOCR_POLL_TIMEOUT=900
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
```

## 三、输入和输出

支持上传：

- `.pdf`：最推荐。系统会自动 OCR 成 Markdown。
- `.docx`：可用。若是 OCR 后生成的 docx，效果较好；若是 Word 原生复杂表格，表格结构可能弱化。
- `.txt`：可用。适合已经整理好的纯文本合同。

输出位置：

```text
data/results/合同提取汇总.xlsx
```

中间产物：

```text
data/uploads/        # 上传文件
data/ocr_texts/      # OCR 或共享文本 Markdown
data/reviews/        # 复盘报告
data/reports/        # 工作报告
Addition/output/     # 附表子项目独立输出
```

这些都是运行时数据，通常不提交到 Git。

## 四、项目结构

```text
合同提取项目/
├── src/
│   ├── webapp.py                  # Web 服务入口、上传/提取/验证/导出接口
│   ├── ocr_client.py              # PaddleOCR 客户端，PDF -> Markdown
│   ├── llm_client.py              # LLM 客户端
│   ├── stage1_fact_extraction.py  # 主表 Stage 1：事实抽取
│   ├── stage2_standardizer.py     # 主表 Stage 2：标准化映射
│   ├── field_knowledge_base.py    # 主表字段规则、允许值、映射逻辑
│   ├── few_shot_examples.py       # 少样本示例
│   ├── excel_writer.py            # 主表/附表合并写 Excel
│   └── addition_bridge.py         # 调用 Addition 附表项目的隔离桥接层
├── Addition/
│   ├── config.yaml                # 附表项目配置
│   ├── companies/youzheng.md      # 附表邮政规则，会注入 LLM prompt
│   ├── field_map.yaml             # 附表字段映射
│   ├── layers/                    # 附表抽取、计算、校验、写表逻辑
│   └── shared_ocr_runner.py       # 接收共享 OCR Markdown 后运行附表流程
├── web/
│   ├── templates/index.html       # 前端页面
│   └── static/style.css           # 前端样式
├── docs/                          # 业务规则和项目说明文档
├── data/                          # 运行时数据，Git 忽略
├── requirements.txt
├── .env.example
└── README.md
```

## 五、主表和附表是否会互相影响

不会。

当前融合方式是：

```text
同一份合同文件
  -> PDF 时先 OCR 成 Markdown
  -> 主表复制一份文本走主表流程
  -> 附表复制一份文本走 Addition 流程
  -> 最后只在前端展示和 Excel 输出时合并
```

主表不会读取附表的 `youzheng.md`，附表也不会读取主表的 `field_knowledge_base.py`。两套业务规则不在同一个层级里互相污染。

## 六、开发者模式

前端默认是普通用户模式，只显示：

- 上传合同
- 并行提取进度
- 主表/附表结果
- 下载 Excel

右上角点击 **开发者模式** 后，会显示调试功能：

- API Key / Provider
- 验证参考答案
- 复盘报告
- 学习精灵
- LLM 复核
- 处理日志

这些功能主要用于调试和项目展示，普通使用不需要打开。

## 七、常见报错

### 1. `DEEPSEEK_API_KEY 未设置`

原因：没有创建 `.env`，或 `.env` 里没有填 DeepSeek Key。

修复：

```bash
cp .env.example .env
```

然后编辑 `.env`：

```env
DEEPSEEK_API_KEY=你的DeepSeekKey
```

### 2. `PADDLEOCR_ACCESS_TOKEN 未配置`

原因：上传 PDF 时需要 PaddleOCR token，但 `.env` 未配置。

修复：

```env
PADDLEOCR_ACCESS_TOKEN=你的PaddleOCRToken
PADDLEOCR_MODEL=PaddleOCR-VL-1.5
```

### 3. 上传 PDF 后 OCR 超时

可能原因：

- PDF 页数较多。
- PaddleOCR 服务排队。
- 网络或 API token 异常。

可在 `.env` 中增大等待时间：

```env
PADDLEOCR_POLL_TIMEOUT=1800
```

### 4. `No module named openpyxl / yaml / pydantic / requests`

原因：依赖没装完整。

修复：

```bash
pip install -r requirements.txt
```

### 5. 附表没有结果

常见原因：

- 合同不是邮政类合同。
- OCR Markdown 中表格结构丢失。
- 原合同没有带价格的线路明细。
- Addition 子项目的依赖或配置缺失。

排查重点：

- `Addition/companies/youzheng.md` 是否存在。
- `Addition/config.yaml` 是否存在。
- `Addition/templates/合同台账表_新(3).xlsx` 是否存在。
- `.env` 是否有 `DEEPSEEK_API_KEY`。

### 6. Git 提醒 CRLF 行分隔符

这是换行格式提示，不是代码错误。建议源码统一使用 `LF`。如果 PyCharm 提示 CRLF，可以选择转换为 `LF - Unix and macOS` 后再提交。

## 八、Git 提交注意事项

不要提交：

```text
.env
Addition/.env
data/
Addition/output/
__pycache__/
.pytest_cache/
.DS_Store
.idea/
```

必须提交：

```text
src/
Addition/               # 附表子项目源码必须提交
Addition/config.yaml
Addition/field_map.yaml
Addition/companies/
Addition/data/__init__.py
Addition/data/geo.py
Addition/templates/合同台账表_新(3).xlsx
web/
requirements.txt
.env.example
README.md
```

如果别人 clone 后缺少 `Addition/`，则附表提取无法运行。

附表项目的高频漏提交文件：

```text
Addition/data/geo.py
Addition/templates/合同台账表_新(3).xlsx
Addition/companies/youzheng.md
Addition/companies/ledger.md
Addition/field_map.yaml
```

其中 `Addition/templates/合同台账表_新(3).xlsx` 是写附表 Excel 时复制的模板，缺失会报：

```text
FileNotFoundError: ... Addition/templates/合同台账表_新(3).xlsx
```

`Addition/data/geo.py` 是省市校验词典，缺失会报：

```text
No module named data
```

## 九、推荐测试流程

1. 启动服务：

```bash
python3 src/webapp.py
```

2. 打开：

```text
http://localhost:8080
```

3. 上传一份 PDF 合同。

4. 等待 OCR、主表、附表完成。

5. 在页面查看主表/附表预览。

6. 点击 `下载 Excel`。

7. 检查 `data/results/合同提取汇总.xlsx`，确认有 `主表` 和 `附表` 两个 sheet。

## 十、补充说明

- 本项目的核心不是通用 Agent，而是面向合同字段提取的业务规则系统。
- 主表规则主要在 `src/field_knowledge_base.py` 和 `src/webapp.py` 的硬校验中。
- 附表规则主要在 `Addition/companies/youzheng.md` 和 `Addition/field_map.yaml` 中。
- `.env` 存放真实 API Key，必须本地创建，不能上传 Git。
