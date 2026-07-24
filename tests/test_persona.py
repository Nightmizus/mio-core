from pydantic import ValidationError

from mio_core.persona import MIO_SYSTEM_PROMPT
from mio_core.schemas import ChatRequest


def test_mio_persona_has_identity_and_operational_boundaries():
    assert "水澄澪音" in MIO_SYSTEM_PROMPT
    assert "水澄是你最喜欢" in MIO_SYSTEM_PROMPT
    assert "像一个真正的人那样聊天" in MIO_SYSTEM_PROMPT
    assert "确定性后台" in MIO_SYSTEM_PROMPT
    assert "不能访问或猜测服务器路径" in MIO_SYSTEM_PROMPT


def test_chat_model_defaults_to_flash_and_accepts_pro():
    assert ChatRequest(content="你好").model == "deepseek-v4-flash"
    assert ChatRequest(content="你好", model="deepseek-v4-pro").model == "deepseek-v4-pro"


def test_chat_model_rejects_unknown_values():
    try:
        ChatRequest(content="你好", model="not-a-model")
    except ValidationError:
        return
    raise AssertionError("unknown model must be rejected")
