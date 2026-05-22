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
    def __init__(self, payload, usage=None):
        self.content = [FakeBlock(payload)]
        self.usage = usage or FakeUsage()


class FakeMessages:
    def __init__(self, payload):
        self._payload = payload
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        return FakeResponse(self._payload)


class FakeClient:
    def __init__(self, payload):
        self.messages = FakeMessages(payload)


@pytest.fixture
def fake_judge_client():
    def _make(verdict="pass", violated=None, reasoning="ok"):
        return FakeClient({"verdict": verdict, "violated": violated or [], "reasoning": reasoning})
    return _make
