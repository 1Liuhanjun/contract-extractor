"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LLM API 客户端
支持 Anthropic Claude、DeepSeek、DeepSeek V4 Pro、OpenAI 兼容接口
"""
import json
import os
import re
import sys
import urllib.request

# 从 .env 文件加载 API Key
try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    if os.path.exists(env_path):
        load_dotenv(env_path)
except ImportError:
    pass


class LLMClient:
    """统一的LLM调用接口"""

    def __init__(self, provider="anthropic", api_key=None, model=None):
        self.provider = provider

        if provider == "anthropic":
            self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            self.model = model or "claude-sonnet-4-20250514"
            self.api_url = "https://api.anthropic.com/v1/messages"
        elif provider in ("deepseek", "deepseek-v4-pro"):
            self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
            if provider == "deepseek-v4-pro":
                self.model = model or "deepseek-v4-pro"
            else:
                self.model = model or "deepseek-chat"
            self.api_url = "https://api.deepseek.com/chat/completions"
        elif provider == "openai":
            self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
            self.model = model or "gpt-4o"
            base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
            self.api_url = f"{base_url}/chat/completions"
        else:
            raise ValueError(f"不支持的provider: {provider}")

    def call(self, system_prompt, user_prompt, temperature=0.1, max_tokens=4096):
        """调用LLM"""
        if not self.api_key:
            env_hint = "DEEPSEEK_API_KEY" if self.provider == "deepseek-v4-pro" else f"{self.provider.upper()}_API_KEY"
            print(f"\n[配置] 请设置 {env_hint} 环境变量或在 .env 中填写")
            return None

        headers = {"Content-Type": "application/json"}

        if self.provider == "anthropic":
            headers["x-api-key"] = self.api_key
            headers["anthropic-version"] = "2023-06-01"
            data = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}]
            }
        else:
            headers["Authorization"] = f"Bearer {self.api_key}"
            data = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
            }
            # DeepSeek V4 Pro 默认启用思考模式，导致 content 为空
            # 显式禁用思考模式以便提取 JSON
            if self.provider in ("deepseek", "deepseek-v4-pro"):
                data["thinking"] = {"type": "disabled"}
            # OpenAI 支持 response_format
            if self.provider == "openai":
                data["response_format"] = {"type": "json_object"}

        try:
            # 自己控制JSON序列化+解码，绕过httpx的编码猜测
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                self.api_url, data=body, headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
            # 强制定为UTF-8
            result = json.loads(raw.decode("utf-8"))

            if self.provider == "anthropic":
                content = result["content"][0]["text"]
            else:
                msg = result["choices"][0]["message"]
                content = msg.get("content", "")
                # DeepSeek V4 Pro 思考模式下 content 可能为空，fallback 到 reasoning_content
                if not content and "reasoning_content" in msg:
                    content = msg["reasoning_content"]

            return self._parse_json(content)
        except Exception as e:
            print(f"  [错误] API调用失败: {e}")
            return None

    def _parse_json(self, content):
        """从LLM回复中解析JSON"""
        content = content.strip()

        # 尝试提取 ```json ... ``` 代码块
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
        if m:
            content = m.group(1).strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # 检查是否截断
            stripped = content.rstrip()
            if not stripped.endswith("}") and not stripped.endswith("]"):
                print(f"  [截断] LLM返回被截断（{len(content)}字符），末20: ...{content[-20:]}")
                return None

            # 尝试修复
            start = content.find('{')
            end = content.rfind('}')
            if start != -1 and end != -1:
                try:
                    return json.loads(content[start:end + 1])
                except json.JSONDecodeError:
                    pass
            print(f"  [错误] 无法解析JSON: {content[:200]}")
            return None
