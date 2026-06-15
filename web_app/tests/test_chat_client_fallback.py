from pathlib import Path


def test_chat_template_has_socketio_polling_fallback():
    html = Path("web_app/templates/scanner.html").read_text(encoding="utf-8")

    assert "function _createPollingSocket()" in html
    assert "_createPollingSocket()" in html[html.index("function _initSocket()") :]
