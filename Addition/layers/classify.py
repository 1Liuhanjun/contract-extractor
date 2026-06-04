"""第1层 公司分类：关键词命中计数 + 置信度。一期只需判「邮政」。"""
import logging

log = logging.getLogger("classify")


def classify_company(full_text: str, cfg) -> tuple:
    """返回 (company, confidence)。company ∈ {邮政, 京东, 未知}。"""
    text = full_text or ""
    yz_kw = cfg.classify.get("youzheng_keywords", [])
    jd_kw = cfg.classify.get("jingdong_keywords", [])
    threshold = cfg.classify.get("threshold", 1)

    yz = sum(text.count(k) for k in yz_kw)
    jd = sum(text.count(k) for k in jd_kw)
    log.info("分类命中：邮政=%d 京东=%d", yz, jd)

    if yz >= threshold and yz >= jd:
        conf = min(0.99, 0.6 + 0.05 * yz)
        return "邮政", round(conf, 2)
    if jd >= threshold and jd > yz:
        conf = min(0.99, 0.6 + 0.05 * jd)
        return "京东", round(conf, 2)
    return "未知", 0.0
