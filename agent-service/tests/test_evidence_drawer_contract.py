from app.ui import login_shell


def test_login_shell_contains_lazy_accessible_evidence_drawer_contract():
    html = login_shell("http://business")
    for marker in (
        "artifact-opened",
        "preventDefault()",
        "/api/runs/",
        "/evidence",
        "evidence-drawer",
        "aria-label=\"关闭答案依据\"",
        "event.key === 'Escape'",
        "@media (max-width: 720px)",
        "正在加载答案依据",
        "本次请求未启用 v2 证据链",
        "无权查看此运行的依据",
    ):
        assert marker in html
