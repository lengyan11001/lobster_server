from backend.app.api.chat import _has_fake_text_tool_call, _parse_text_tool_calls, _strip_dsml
from backend.app.api.sutui_chat_proxy import _response_has_fake_tool_text, _strip_fake_tool_text_from_response


def test_double_bar_dsml_tool_calls_is_fake_not_executed():
    content = (
        "任务已成功提交！\n\n"
        "<｜｜DSML｜｜tool_calls>\n"
        "<｜｜DSML｜｜invoke name=\"lobster__invoke_capability\">\n"
        "<｜｜DSML｜｜parameter name=\"capability_id\" string=\"true\">comfly.daihuo.pipeline</｜｜DSML｜｜parameter>\n"
        "<｜｜DSML｜｜parameter name=\"payload\" string=\"false\">{\"action\":\"poll_pipeline\",\"job_id\":\"86cbfb322b3a481bbe29be7574ba7b12\"}</｜｜DSML｜｜parameter>\n"
        "</｜｜DSML｜｜invoke>\n"
        "</｜｜DSML｜｜tool_calls>"
    )
    assert _has_fake_text_tool_call(content)
    assert _parse_text_tool_calls(content) == []
    assert "86cbfb322b3a481bbe29be7574ba7b12" not in _strip_dsml(content)


def test_sutui_chat_proxy_detects_and_strips_double_bar_dsml():
    data = {"choices": [{"message": {"content": "ok <｜｜DSML｜｜tool_calls>bad</｜｜DSML｜｜tool_calls>"}}]}
    assert _response_has_fake_tool_text(data)
    assert _strip_fake_tool_text_from_response(data)
    assert "DSML" not in data["choices"][0]["message"]["content"]
