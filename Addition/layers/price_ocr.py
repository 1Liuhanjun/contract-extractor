"""价格表数字交叉复核（v2 §13.2）：用第二套本地 OCR(RapidOCR) 复核托管 OCR 读出的价格。

背景：业务大模型是 DeepSeek 纯文本、无 VLM 兜底；价格数字精度只能靠 OCR 层保证。
本层在「托管 PaddleOCR-VL 已产出价格」之后，用一个**独立引擎**对价格表所在页做二次识别，
把两套引擎读到的数字做交叉比对：托管侧的直读价若在本地引擎结果中找不到佐证 → 标低置信、进复核。

工程约束：
- 托管 OCR 不回传逐格 bbox，无法做"单元格裁剪+数字白名单"那种精细对齐；这里退而求其次，
  做"价格表所在页的数字集合交叉"——同一数字两引擎都出现即互证，仅托管侧出现即存疑。
- 可选层：cfg.enhance.cross_check_prices=true 才启用；RapidOCR 未安装则告警降级（不报错）。
- DPI 自适应：默认整页较高 DPI 重渲染；命中失败可由调用方按需再升采样（接口已留）。
"""
import logging

log = logging.getLogger("price_ocr")


def _try_import_rapidocr():
    try:
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR
    except Exception:  # noqa
        return None


def _render_pages(pdf_path, pages, dpi):
    """用 PyMuPDF 渲染指定页码(1-based)为 PNG 字节，返回 {page: bytes}。"""
    import fitz  # PyMuPDF
    out = {}
    doc = fitz.open(pdf_path)
    try:
        for p in sorted(set(pages)):
            if 1 <= p <= doc.page_count:
                pix = doc[p - 1].get_pixmap(dpi=dpi)
                out[p] = pix.tobytes("png")
    finally:
        doc.close()
    return out


def _numbers_on_image(engine, png_bytes):
    """对一张图跑 RapidOCR，收集出现过的数字串集合（保留 2 位小数与整数两种形态）。"""
    import numpy as np
    import cv2
    arr = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
    result, _ = engine(arr)
    nums = set()
    for line in (result or []):
        text = line[1] if isinstance(line, (list, tuple)) and len(line) > 1 else str(line)
        for tok in _extract_numbers(text):
            nums.add(tok)
    return nums


def _extract_numbers(text):
    import re
    out = []
    for m in re.findall(r"\d+(?:\.\d+)?", str(text).replace(",", "")):
        out.append(m)
        try:
            f = float(m)
            out.append(f"{f:.2f}")
            if f == int(f):
                out.append(str(int(f)))
        except ValueError:
            pass
    return out


def _fmt(v):
    """把价格数值格式化成可比较的多种字符串形态。"""
    forms = set()
    try:
        f = float(v)
        forms.add(f"{f:.2f}")
        forms.add(f"{f:g}")
        if f == int(f):
            forms.add(str(int(f)))
    except (TypeError, ValueError):
        forms.add(str(v))
    return forms


def cross_check(pdf_path, result, doc, enhance):
    """交叉复核直读价格；返回告警列表（写入复核报告）。

    只对"直读价格"(吨位价格里 LLM 直接读出的数字)做佐证；换算出来的价格不查
    （它们是程序算的，不是 OCR 直接读的）。
    """
    if not enhance.get("cross_check_prices"):
        return []
    Engine = _try_import_rapidocr()
    if Engine is None:
        log.warning("未安装 rapidocr_onnxruntime，跳过价格数字交叉复核"
                    "（pip install rapidocr_onnxruntime opencv-python 可启用）。")
        return ["（提示）价格数字交叉复核已配置但未安装 RapidOCR，本次未执行二次校验。"]

    # 需要复核的页：线路所在页 + 表格所在页
    pages = [r.page for r in result.routes if r.page] + [t.page for t in doc.tables if t.page]
    if not pages:
        return []
    dpi = int(enhance.get("cross_check_dpi", 400))
    try:
        imgs = _render_pages(pdf_path, pages, dpi)
        engine = Engine()
        page_nums = {p: _numbers_on_image(engine, b) for p, b in imgs.items()}
    except Exception as e:  # noqa
        log.warning("价格交叉复核渲染/识别失败，跳过：%s", e)
        return [f"（提示）价格数字交叉复核执行失败，未完成二次校验：{e}"]

    # 全部页数字并集（线路 page 可能不准，放宽到所有复核页）
    all_nums = set().union(*page_nums.values()) if page_nums else set()
    warnings = []
    for i, r in enumerate(result.routes):
        direct = r.吨位价格 or {}
        for h, v in direct.items():
            if v in (None, "", "/"):
                continue
            forms = _fmt(v)
            local = page_nums.get(r.page, set()) | all_nums
            if not (forms & local):
                r.confidence = min(r.confidence or 1, 0.5)
                warnings.append(f"⚠️ 价格交叉复核：第{i+1}条线路「{r.线路名称}」{h}={v} "
                                f"未被本地 RapidOCR 佐证，疑似托管 OCR 误读，请人工核对原图。")
    if not warnings:
        log.info("价格数字交叉复核通过：%d 条线路直读价均获本地引擎佐证。", len(result.routes))
    return warnings
