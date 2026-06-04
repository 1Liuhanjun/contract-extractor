"""LLM provider 抽象 + DeepSeek 实现（OpenAI 兼容 /chat/completions）。

call_json(system, user) → dict：强制 JSON 输出 + 自动重试。
切换 provider 只需实现 LLMProvider 接口并在 config 改 provider。
"""
import re
import json
import time
import logging
import requests

log = logging.getLogger("llm")


class LLMError(Exception):
    pass


class LLMProvider:
    def call_json(self, system: str, user: str, temperature: float = None, label: str = "") -> dict:
        raise NotImplementedError


class DeepSeekProvider(LLMProvider):
    def __init__(self, base_url, model, api_key, temperature=0.0, timeout=180,
                 max_retries=3, max_tokens=8192, use_response_format=True):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_tokens = max_tokens   # 输出上限（chat 8192；V4 可达 384K）
        # 思考型/推理型模型（如 V4-pro 思考模式、旧 reasoner）通常不支持强制 JSON；
        # 关掉则改用 prompt 约束 + 容错解析，避免 400。
        self.use_response_format = use_response_format
        self.call_count = 0
        # —— token 用量统计（外接 API 可观测）——
        self.total_tokens = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def call_json(self, system: str, user: str, temperature: float = None, label: str = "") -> dict:
        if not self.api_key:
            raise LLMError("DEEPSEEK_API_KEY 未设置（检查 .env）")
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # temperature 可被单次调用覆盖（self-consistency 投票时用 >0）
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
            # 速度优化(P0-1)：关闭 DeepSeek 思考模式。实测思考模式单次 ~45s，关闭后 ~7s；
            # 对"从文本抄字段"类结构化抽取准确率影响极小（同事仓库已验证可关）。
            "thinking": {"type": "disabled"},
        }
        if self.use_response_format:
            payload["response_format"] = {"type": "json_object"}
        last_err = None
        for attempt in range(1, self.max_retries + 1):
            try:
                self.call_count += 1
                resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
                if resp.status_code != 200:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                usage = data.get("usage") or {}
                self._log_usage(usage, label)
                # 输出触顶 → 大概率被截断成残缺 JSON，明确告警（提示该块需更紧凑或分块）
                finish = (data.get("choices") or [{}])[0].get("finish_reason")
                if finish == "length" or usage.get("completion_tokens") == self.max_tokens:
                    log.warning("[%s] 输出达到 max_tokens=%d 被截断，JSON 可能残缺（将尝试容错解析）。",
                                label or "?", self.max_tokens)
                # 思考型模型会另有 reasoning_content（思维链），最终答案在 content
                msg = (data.get("choices") or [{}])[0].get("message", {}) or {}
                content = msg.get("content") or ""
                if not content and msg.get("reasoning_content"):
                    log.warning("[%s] content 为空、仅有 reasoning_content；模型可能未给出最终答案。", label or "?")
                return _safe_json(content)
            except Exception as e:  # noqa
                last_err = e
                log.warning("DeepSeek 调用失败(第%d/%d次): %s", attempt, self.max_retries, e)
                time.sleep(min(2 ** attempt, 10))
        raise LLMError(f"DeepSeek 连续 {self.max_retries} 次失败: {last_err}")

    def _log_usage(self, usage: dict, label: str):
        """解析并记录单次 token 用量 + 维护累计。DeepSeek 还返回缓存命中/未命中。"""
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        tt = usage.get("total_tokens")
        if tt is None:
            log.warning("[LLM用量] %s 响应未含 usage 字段，无法统计 token。", label or "?")
            return
        self.total_prompt_tokens += pt or 0
        self.total_completion_tokens += ct or 0
        self.total_tokens += tt or 0
        hit = usage.get("prompt_cache_hit_tokens")
        miss = usage.get("prompt_cache_miss_tokens")
        cache = f"，缓存命中={hit}/未命中={miss}" if hit is not None else ""
        log.info("[LLM用量] %-14s 本次 prompt=%s completion=%s total=%s%s ｜ 累计 total=%d（调用%d次）",
                 (label or "?"), pt, ct, tt, cache, self.total_tokens, self.call_count)


def _safe_json(content: str) -> dict:
    """容错解析 JSON：
    1) 剥离 ```json 包裹；
    2) strict=False 允许字符串内含原始控制字符（修「Invalid control character」，系数表常见）；
    3) 若被 max_tokens 截断成残缺 JSON，尽量抢救出已完整的数组元素（修线路明细截断）。
    """
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:]
    content = content.strip()
    # 1+2：直接（宽松）解析
    try:
        return json.loads(content, strict=False)
    except json.JSONDecodeError:
        pass
    # 截取第一个 { 到最后一个 }
    s, e = content.find("{"), content.rfind("}")
    if s != -1 and e != -1 and e > s:
        try:
            return json.loads(content[s:e + 1], strict=False)
        except json.JSONDecodeError:
            pass
    # 3：抢救被截断的数组（如 routes 输出触顶）——保留所有已完整的 {…} 元素
    salvaged = _salvage_truncated(content)
    if salvaged is not None:
        log.warning("JSON 残缺，已抢救出 %d 条数组元素（其余因截断丢弃）。",
                    len(next(iter(salvaged.values()), [])) if isinstance(salvaged, dict) else 0)
        return salvaged
    raise json.JSONDecodeError("无法解析模型输出为 JSON", content[:200], 0)


def _salvage_truncated(content: str):
    """从被截断的 JSON 里抢救出 "<key>": [ {…}, {…}, … ] 中已完整闭合的对象。

    返回 {key: [obj,...], found: True} 或 None。用于线路明细输出超 max_tokens 被切断的情况。
    """
    m = re.search(r'"(\w+)"\s*:\s*\[', content)
    if not m:
        return None
    key = m.group(1)
    i = content.index("[", m.end() - 1) + 1
    objs, depth, start, in_str, esc = [], 0, None, False, False
    for j in range(i, len(content)):
        c = content[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            if depth == 0:
                start = j
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start is not None:
                frag = content[start:j + 1]
                try:
                    objs.append(json.loads(frag, strict=False))
                except json.JSONDecodeError:
                    pass
                start = None
        elif c == "]" and depth == 0:
            break
    if not objs:
        return None
    return {key: objs, "found": True}


def build_llm(cfg) -> LLMProvider:
    provider = cfg.llm.get("provider", "deepseek")
    if provider == "deepseek":
        return DeepSeekProvider(
            base_url=cfg.llm["base_url"],
            model=cfg.llm["model"],
            api_key=cfg.llm_key,
            temperature=cfg.llm.get("temperature", 0.0),
            timeout=cfg.llm.get("timeout", 180),
            max_retries=cfg.llm.get("max_retries", 3),
            max_tokens=cfg.llm.get("max_tokens", 8192),
            use_response_format=cfg.llm.get("use_response_format", True),
        )
    raise LLMError(f"未知 LLM provider: {provider}")
