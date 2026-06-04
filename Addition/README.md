# 邮政合同读取智能体（方案2 · 纯 OCR 路线）

把扫描版的邮政运输合同 PDF，自动读成结构化的 Excel 台账，并给出一份待人工复核的清单。

> **本目录自包含**：模板（`templates/`）、样例合同（`samples/`）、输入（`input/`）、输出（`output/`）全在代码目录内。
> 拿到这个目录的压缩包，装好依赖、填好密钥即可直接运行，无需任何外部文件。

**流程**：托管 PaddleOCR-VL 把 PDF 转成文字+表格 → DeepSeek（纯文本大模型）抽取字段 →
跨吨位价格换算 → 校验 → 累加写入 Excel 台账 → 生成复核报告。**全程不把图片发给大模型**（无 VLM）。

> 延伸阅读（项目文档，运行不依赖，可能不在代码压缩包内）：详细设计见 `../文档/最终技术方案 v2.md`，字段口径见 `../../需求文档.md`。

---

## 🚀 快速开始（5 步跑通）

```powershell
cd "方案2 ocr\代码"
pip install -r requirements.txt          # 1. 装依赖（核心仅 4 个包）
copy .env.example .env                    # 2. 复制后编辑 .env，填 DeepSeek 与 PaddleOCR 两个密钥
#    关掉 VPN（OCR/DeepSeek 都是国内服务，VPN 会让上传超时）   3.
python diag.py                            # 4. 验证联通（默认跑 samples\浙江.pdf），看 output\diag.log
#    把要处理的合同 PDF 放进 input\ 目录                       5.
python run.py                             # 处理 input\ 下所有 PDF，结果在 output\
```

下面是各步骤的详细说明。

---

## 一、环境要求

- **Python 3.10+**（推荐 Anaconda，Windows 11 主环境）
- 两个外部服务的账号 / 密钥：
  - **DeepSeek**（抽取用的大模型）— https://platform.deepseek.com 申请 API Key
  - **百度 AI Studio PaddleOCR-VL**（OCR）— https://aistudio.baidu.com/paddleocr/task 部署应用后拿到 Access Token 和 API_URL

---

## 二、安装

```powershell
cd "E:\实习工作\博华物流\合同读取agent\方案2 ocr\代码"
pip install -r requirements.txt
```

核心依赖只有 4 个（requests / openpyxl / pydantic / PyYAML），Anaconda 多半已自带。
`requirements.txt` 里注释掉的是**可选**项（仅当开启价格数字二次交叉复核时才装，见 §六）。

---

## 三、配置（只需 2 步）

### 1) 填密钥 → `.env`

复制 `.env.example` 为 `.env`，填入你自己的两个密钥：

```
DEEPSEEK_API_KEY=sk-你的deepseek密钥
PADDLEOCR_ACCESS_TOKEN=你的paddleocr访问令牌
```

> 密钥只从环境变量/`.env` 读取，**绝不写进日志或配置文件**。`.env` 不要提交到版本库。

### 2) 确认 OCR 地址 → `config.yaml`

`config.yaml` 的 `ocr.base_url` 已填入当前 AI Studio 的 jobs 端点：

```yaml
ocr:
  base_url: "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
  model: "PaddleOCR-VL-1.6"
```

如果你的 AI Studio 任务页「API 调用示例」里的 `JOB_URL` 不同，把 `base_url` 改成你自己的那一条即可（原样粘贴整条 URL）。其余项一般无需改。

---

## 四、运行

> ⚠️ **重要：连接前请关闭 VPN！** OCR 和 DeepSeek 都是**国内服务**，挂着 VPN 走境外出口会导致
> 大文件（PDF 约 2MB）上传卡死超时。小请求能秒回、但大上传超时，几乎都是 VPN 问题。

### 第一次：先用 `diag.py` 验证联通（推荐）

它分阶段跑、把每一步结果和任何报错的完整堆栈都写进一个固定日志，方便排错：

```powershell
python diag.py                       # 默认跑 samples\浙江.pdf
python diag.py samples\福建.pdf       # 指定某份样例
python diag.py input\你的合同.pdf     # 指定自己的 PDF
```

日志固定写到 **`output\diag.log`**。看到 `[OCR] ✅ 成功` 和 `[PIPELINE] ✅ 结果` 就说明通了。

> 同一份 PDF 的 OCR 结果会按内容缓存，**重跑会秒过 OCR**（不再等几十秒），直接进 DeepSeek 抽取——
> 所以排查 DeepSeek 问题时反复跑 `diag.py` 不会重复消耗 OCR。

### 正式使用：`run.py`

```powershell
python run.py                              # 不带参数：处理 input\ 目录下所有 PDF（推荐）
python run.py samples\浙江.pdf              # 单文件
python run.py D:\某目录\                     # 批量：处理指定目录下所有 PDF
python run.py samples\浙江.pdf -o D:\台账\主表.xlsx   # 指定输出位置
```

**日常用法**：把待处理的合同 PDF 丢进 `input\` 目录，运行 `python run.py` 即可，结果都在 `output\`。

---

## 五、输出产物

默认都在 `output/` 下：

| 产物 | 路径 | 说明 |
| --- | --- | --- |
| 主台账 Excel | `output/合同台账主表.xlsx` | **累加式**：首次从模板复制结构并清空数据行，之后每份合同 append |
| 复核报告 | `output/reviews/<合同名>_review.md` | 留空原因/低置信处置/冲突候选/换算依据/校验告警/**token 用量** |
| 运行日志 | `output/logs/run.log` + `output/logs/<合同名>.log` | 每份合同单独一份日志 |
| OCR 缓存 | `output/_cache/<合同名>_<sha1前10位>/ocr.json` | 按文件内容 sha1 缓存并校验，重跑同一文件不重复 OCR、同名不同内容不会串档 |
| 去重索引 | `output/_index.json` | 记录已处理文件（sha1+合同编号），防重复 append |
| 未知公司 | `output/_unknown/` | 分类判不出邮政的 PDF 拷到这里待人工确认 |

**台账写入规则**：`新合同台账` sheet 每份合同 1 行；`邮政` sheet 每条线路 1 行（N 行）。

### Token 用量（外接大模型可观测）

每次 DeepSeek 调用都会在日志记一行，标明是哪个抽取块：

```
[LLM用量] 元信息/条款   本次 prompt=4521 completion=380 total=4901，缓存命中=3200/未命中=1321 ｜ 累计 total=4901（调用1次）
```

每份合同的总 token 用量也会写进该合同的复核报告（「输入信息」里的 `LLM token 用量`）。

---

## 六、配置详解（`config.yaml`）

```yaml
llm:                       # 抽取大模型（DeepSeek 纯文本）
  base_url: "https://api.deepseek.com"
  model: "deepseek-v4-pro"   # V4 推理模型（旧 deepseek-chat/reasoner 2026-07-24 弃用）。flash 更快更便宜
  max_tokens: 32768          # V4 支持到 384K；解决"线路多被截断"
  use_response_format: false # 思考模式可能不接受强制 JSON → 关掉，改用 prompt 约束+容错解析；实测支持可设 true
  temperature: 0

classify:                  # 公司分类关键词
  youzheng_keywords: [...] # 命中即判邮政
supported_companies: ["邮政"]   # 一期仅处理邮政；二期加京东见代码 TODO

enhance:                   # 准确率增强（详见技术方案 §13）
  evidence_recheck: true   # 字段证据必须在 OCR 原文命中，否则降置信进复核（反幻觉）
  self_consistency: true   # 低置信字段多次抽取投票取多数
  cross_check_prices: false  # 价格数字第二引擎(RapidOCR)交叉复核——默认关
  price_range: [1, 1000000]  # 价格区间校验

defaults:                  # 固定值字段（红字系统外字段的默认）
  客户分类: "邮政"
  合同主体: "北京博华物流有限公司"

paths:                     # 路径（相对本代码目录解析，默认全在目录内，自包含）
  template_xlsx: "templates/合同台账表_新(3).xlsx"
  input_dir: "./input"     # run.py 无参时处理这里的 PDF
  output_dir: "./output"   # 所有产物统一输出到这里
```

**开启价格数字交叉复核**（精度兜底，可选）：把 `cross_check_prices` 设为 `true`，并安装可选依赖：

```powershell
pip install rapidocr_onnxruntime PyMuPDF opencv-python numpy
```

它会用第二套本地 OCR 引擎复核托管 OCR 读出的价格，对不上就标低置信进复核。

**字段映射** `field_map.yaml`：声明每一列「从哪来/要不要抽/怎么填」（红字留空、固定值、公式、吨位换算）。换字段只改这里，不动代码。

---

## 七、常见问题（排错）

| 现象 | 原因 / 解决 |
| --- | --- |
| OCR 提交一直 `Read timed out` | **十有八九是 VPN**。关掉 VPN 再跑。小请求通、大上传断 = VPN 出口瓶颈 |
| `模型传参错误` / 400 | OCR 请求参数不对；确认用的是最新 `providers/ocr.py` 且 `base_url` 是完整 jobs 端点 |
| DeepSeek `HTTP 401 Authentication Fails ... api key invalid` | **不是网络问题，是 key 无效**。去 https://platform.deepseek.com 确认 key 正确/未过期、账户有余额，必要时重新生成；检查 `.env` 没多余空格换行 |
| `DEEPSEEK_API_KEY 未设置` | `.env` 没填或没在代码目录运行 |
| 某条线路价格全空 + 报告强告警 | 该合同 PDF 内本就没有逐线路单价（如武汉样例）。这是**正确行为**，禁止臆造，需人工补价格来源 |
| 公司被判「未知」 | PDF 没命中邮政关键词；文件被拷到 `output/_unknown/`，人工确认 |
| 重复运行同一份 PDF | 自动跳过（`_index.json` 去重），不会重复 append |
| 提示「上次处理中断」 | 上次写表中途崩溃；检查主表后，如需重跑先从 `output/_index.json` 删掉该条目 |

---

## 八、测试（离线，无需任何 API key）

```powershell
python -m pytest tests/ -v
```

- `test_pipeline_offline.py`：Mock provider 跑通端到端（换算数值/Excel 结构/P 列公式/去重/未知公司/省级市）
- `test_verify.py`：证据回查 / 枚举归一 / 投票
- `test_fixes.py`：缓存 sha1 校验防串档 / 小数吨位 / 不一致放弃换算

> 测试输出全部隔离到临时目录，**不会污染正式 `output/` 与模板**；正式运行也**绝不会**调用 Mock。

---

## 九、目录结构

```
方案2 ocr/代码/
  run.py                 # 正式 CLI 入口（无参→处理 input/；或单文件/目录/-o）
  diag.py                # 联调诊断脚本（分阶段+固定日志，首次联通用它）
  config.yaml            # 配置：OCR/LLM/分类/增强/默认值/路径
  field_map.yaml         # 38+30 列声明（红字/默认/公式/换算）
  .env                   # 两个密钥（你自己填，不入库）
  templates/             # 主台账 Excel 模板（自包含）
  samples/               # 三份样例合同 浙江/武汉/福建.pdf（开箱试跑）
  input/                 # 把待处理 PDF 放这里，run.py 无参时处理它
  pipeline.py            # 编排第0~6层
  layers/
    represent.py         # 第0层 调托管 PaddleOCR-VL + sha1 缓存校验
    classify.py          # 第1层 公司分类
    extract.py           # 第2层 LLM 抽取（分块+失败降级）
    verify.py            # 第2层增强 证据回查/枚举/投票
    calculate.py         # 第3层 跨吨位换算 / 省市派生
    validate.py          # 第4层 校验不变量
    price_ocr.py         # §13.2 价格数字交叉复核（可选）
    excel_writer.py      # 第5层 累加+去重
    report.py            # 第6层 复核报告
  providers/
    ocr.py               # 托管 PaddleOCR-VL（异步 jobs：提交→轮询→取结果）
    llm.py               # DeepSeek（含 token 用量统计）
    mock.py              # 离线 Mock（仅测试用）
  data/geo.py            # 省市词典（省市一致校验）
  schemas.py             # Pydantic 字段模型
  companies/
    youzheng.md          # 「邮政」线路表规则（单一规则源，注入线路/系数抽取 prompt）
    ledger.md            # 「新合同台账」字段规则（单一规则源，注入两阶段抽取 prompt）
    ledger_kb.py         # 读 ledger.md 分段 + 拼装两阶段 prompt（不再硬编码规则文本）
  tests/                 # 离线回归测试
  output/                # 运行产物（台账/报告/日志/缓存）
```

---

## 十、已知边界

- **价格 ≤0.5% 精度**：押在托管 OCR + 校验 + 人工复核（无 VLM 兜底）。如不达标，开 `cross_check_prices` 或扩大人工复核（技术方案 §11）。
- **二期换公司（京东等）**：当前引擎按邮政落地，加新公司需扩展 `pipeline`/`field_map`/新 skill（已留 `supported_companies` 配置位与 TODO）。
- 本地预处理/单元格裁剪等：托管 OCR 路线下不做（已在技术方案 §2.0/§13.2 标注）。
