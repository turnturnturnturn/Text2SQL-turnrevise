from app.ui import login_shell


def test_chat_endpoint_is_configured_only_after_login() -> None:
    html = login_shell("http://business-service:8080")

    assert '<vanna-chat id="chat"></vanna-chat>' in html
    assert "chat.setCustomHeaders" in html
    assert "chat.setAttribute('sse-endpoint', '/api/vanna/v2/chat_sse')" in html
    assert html.index("chat.setCustomHeaders") < html.index(
        "chat.setAttribute('sse-endpoint', '/api/vanna/v2/chat_sse')"
    )
