import pytest


class FakeBlock:
    def __init__(self, payload):
        self.type = "tool_use"
        self.name = "record_verdict"
        self.input = payload


class FakeUsage:
    def __init__(self, **kw):
        self.input_tokens = kw.get("input_tokens", 0)
        self.output_tokens = kw.get("output_tokens", 0)
        self.cache_read_input_tokens = kw.get("cache_read_input_tokens", 0)
        self.cache_creation_input_tokens = kw.get("cache_creation_input_tokens", 0)


class FakeResponse:
    def __init__(self, payload, usage=None, stop_reason="tool_use"):
        self.content = [FakeBlock(payload)]
        self.usage = usage or FakeUsage()
        self.stop_reason = stop_reason


class FakeMessages:
    def __init__(self, payload, stop_reason="tool_use"):
        self._payload = payload
        self._stop_reason = stop_reason
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        return FakeResponse(self._payload, stop_reason=self._stop_reason)


class FakeClient:
    def __init__(self, payload, stop_reason="tool_use"):
        self.messages = FakeMessages(payload, stop_reason=stop_reason)


@pytest.fixture
def fake_judge_client():
    def _make(verdict="pass", violated=None, reasoning="ok", stop_reason="tool_use"):
        return FakeClient(
            {"verdict": verdict, "violated": violated or [], "reasoning": reasoning},
            stop_reason=stop_reason,
        )
    return _make
