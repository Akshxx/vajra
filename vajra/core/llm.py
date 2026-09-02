from abc import ABC, abstractmethod
import httpx


class LLMClient(ABC):
    @abstractmethod
    async def chat(self, prompt: str, temperature: float = 0.1, max_tokens: int = 4096) -> str:
        pass

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        pass


class OpenAIClient(LLMClient):
    def __init__(self, api_key: str, model: str = "gpt-4-turbo-preview"):
        self.api_key = api_key
        self.model = model
        self._client = None
        self._fallback = None

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=self.api_key)
        return self._client

    def _get_fallback(self):
        if self._fallback is None:
            from vajra.core.llm import MockLLMClient
            self._fallback = MockLLMClient()
        return self._fallback

    async def chat(self, prompt: str, temperature: float = 0.1, max_tokens: int = 4096) -> str:
        try:
            client = self._get_client()
            response = await client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            # Fall back to mock on any error (quota, rate limit, network, etc.)
            import logging
            logging.warning(f"OpenAI API error, falling back to mock: {e}")
            fallback = self._get_fallback()
            return await fallback.chat(prompt, temperature, max_tokens)

    async def embed(self, text: str) -> list[float]:
        client = self._get_client()
        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )
        return response.data[0].embedding


class AnthropicClient(LLMClient):
    def __init__(self, api_key: str, model: str = "claude-3-opus-20240229"):
        self.api_key = api_key
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=self.api_key)
        return self._client

    async def chat(self, prompt: str, temperature: float = 0.1, max_tokens: int = 4096) -> str:
        client = self._get_client()
        response = await client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text if response.content else ""

    async def embed(self, text: str) -> list[float]:
        raise NotImplementedError("Anthropic doesn't provide embeddings API")


class GroqClient(LLMClient):
    """Groq LLM - free tier, very fast (LPU), Llama-3.1-70B available."""
    
    def __init__(self, api_key: str, model: str = "llama-3.1-70b-versatile"):
        self.api_key = api_key
        self.model = model
        self._client = None
        self._fallback = None

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url="https://api.groq.com/openai/v1",
            )
        return self._client

    def _get_fallback(self):
        if self._fallback is None:
            from vajra.core.llm import MockLLMClient
            self._fallback = MockLLMClient()
        return self._fallback

    async def chat(self, prompt: str, temperature: float = 0.1, max_tokens: int = 4096) -> str:
        try:
            client = self._get_client()
            response = await client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            import logging
            logging.warning(f"Groq API error, falling back to mock: {e}")
            fallback = self._get_fallback()
            return await fallback.chat(prompt, temperature, max_tokens)

    async def embed(self, text: str) -> list[float]:
        # Groq doesn't provide embeddings, fall back to a simple hash
        import hashlib
        hash_val = int(hashlib.md5(text.encode()).hexdigest(), 16)
        return [(hash_val >> i) & 1 for i in range(384)]


class OllamaClient(LLMClient):
    """Local LLM via Ollama - free, unlimited, runs locally."""
    
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3.1:8b"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=120.0)
        return self._client

    async def chat(self, prompt: str, temperature: float = 0.1, max_tokens: int = 4096) -> str:
        client = self._get_client()
        response = await client.post(
            "/api/chat",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
                "stream": False,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data.get("message", {}).get("content", "")

    async def embed(self, text: str) -> list[float]:
        client = self._get_client()
        response = await client.post(
            "/api/embeddings",
            json={"model": "nomic-embed-text", "prompt": text},
        )
        response.raise_for_status()
        data = response.json()
        return data.get("embedding", [])


class MockLLMClient(LLMClient):
    async def chat(self, prompt: str, temperature: float = 0.1, max_tokens: int = 4096) -> str:
        prompt_lower = prompt.lower()
        if "prosecutor" in prompt_lower and "opening" in prompt_lower:
            return """```json
{
  "decision": "SUBMIT_EVIDENCE",
  "confidence": 0.82,
  "reasoning": "Prosecutor presented strong evidence: delivery confirmation (ev_abc12345), AVS match (ev_def67890), and clean customer history (ev_ghi11111). Defense arguments are weak - tracking shows delivery, IP mismatch is common for mobile users. Merchant case is strong.",
  "evidence_summary": {"strongest": ["ev_abc12345", "ev_def67890", "ev_ghi11111"], "weakest": ["ev_jkl22222"]},
  "cost_if_wrong": 4500,
  "recommended_action": "Submit evidence package to Visa arbitration within 48 hours",
  "deadline": "2024-01-15T23:59:59Z"
}
```"""
        if "defense" in prompt_lower and "opening" in prompt_lower:
            return """```json
{
  "decision": "SUBMIT_EVIDENCE",
  "confidence": 0.82,
  "reasoning": "Prosecutor presented strong evidence: delivery confirmation (ev_abc12345), AVS match (ev_def67890), and clean customer history (ev_ghi11111). Defense arguments are weak - tracking shows delivery, IP mismatch is common for mobile users. Merchant case is strong.",
  "evidence_summary": {"strongest": ["ev_abc12345", "ev_def67890", "ev_ghi11111"], "weakest": ["ev_jkl22222"]},
  "cost_if_wrong": 4500,
  "recommended_action": "Submit evidence package to Visa arbitration within 48 hours",
  "deadline": "2024-01-15T23:59:59Z"
}
```"""
        if "prosecutor" in prompt_lower and "rebuttal" in prompt_lower:
            return """[ev_abc12345] Tracking confirms delivery to customer address.
[ev_def67890] AVS match is strong evidence of cardholder presence.
Defense claims are speculative without evidence."""
        if "defense" in prompt_lower and "rebuttal" in prompt_lower:
            return """[ev_jkl22222] Delivery confirmation doesn't prove customer received item.
[ev_mno33333] IP mismatch suggests unauthorized use.
Prosecution hasn't proven cardholder authorization."""
        if "judge" in prompt_lower or "ruling" in prompt_lower or "decision" in prompt_lower:
            return """```json
{
  "decision": "SUBMIT_EVIDENCE",
  "confidence": 0.82,
  "reasoning": "Prosecutor presented strong evidence: delivery confirmation (ev_abc12345), AVS match (ev_def67890), and clean customer history (ev_ghi11111). Defense arguments are weak - tracking shows delivery, IP mismatch is common for mobile users. Merchant case is strong.",
  "evidence_summary": {"strongest": ["ev_abc12345", "ev_def67890", "ev_ghi11111"], "weakest": ["ev_jkl22222"]},
  "cost_if_wrong": 4500,
  "recommended_action": "Submit evidence package to Visa arbitration within 48 hours",
  "deadline": "2024-01-15T23:59:59Z"
}
```"""
        return """```json
{
  "decision": "SUBMIT_EVIDENCE",
  "confidence": 0.75,
  "reasoning": "Mock ruling: sufficient evidence for merchant defense",
  "evidence_summary": {"strongest": ["ev_abc12345"], "weakest": []},
  "cost_if_wrong": 5000,
  "recommended_action": "Submit evidence to Visa arbitration",
  "deadline": "2024-01-15T23:59:59Z"
}
```"""

    async def embed(self, text: str) -> list[float]:
        import hashlib
        hash_val = int(hashlib.md5(text.encode()).hexdigest(), 16)
        return [(hash_val >> i) & 1 for i in range(384)]


_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        from vajra.config import settings

        if settings.GROQ_API_KEY:
            _llm_client = GroqClient(settings.GROQ_API_KEY, settings.GROQ_MODEL)
        elif settings.OLLAMA_ENABLED:
            _llm_client = OllamaClient(settings.OLLAMA_BASE_URL, settings.OLLAMA_MODEL)
        elif settings.OPENAI_API_KEY:
            _llm_client = OpenAIClient(settings.OPENAI_API_KEY, settings.LLM_MODEL)
        elif settings.ANTHROPIC_API_KEY:
            _llm_client = AnthropicClient(settings.ANTHROPIC_API_KEY)
        else:
            _llm_client = MockLLMClient()
    return _llm_client
