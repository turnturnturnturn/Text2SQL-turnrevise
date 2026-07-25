from __future__ import annotations

import json


def login_shell(public_business_service_url: str) -> str:
    login_base = json.dumps(public_business_service_url.rstrip("/"), ensure_ascii=True)
    login_base = login_base.replace("<", "\\u003c").replace(">", "\\u003e")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Enterprise Database Copilot</title>
  <script type="module" src="https://img.vanna.ai/vanna-components.js"></script>
  <style>
    :root {{ color-scheme: light; font-family: Inter, system-ui, sans-serif; }}
    body {{ margin: 0; background: #f3f6f8; color: #17202a; }}
    header {{ display:flex; gap:16px; align-items:center; padding:14px 24px; background:#102a43; color:white; }}
    header strong {{ flex:1; }}
    input, button {{ padding:9px 12px; border-radius:8px; border:1px solid #bcccdc; }}
    button {{ cursor:pointer; background:#2cb1bc; color:white; border:0; font-weight:600; }}
    #logout {{ background:#627d98; display:none; }}
    #status {{ min-width:100px; font-size:13px; }}
    main {{ max-width:1100px; margin:20px auto; padding:0 16px; }}
    #login {{ background:white; padding:18px; border-radius:12px; box-shadow:0 5px 20px #102a4315; }}
    #chat {{ display:none; min-height:650px; }}
    #evidence-drawer {{ position:fixed; inset:0 0 0 auto; width:min(440px,92vw); background:white;
      box-shadow:-8px 0 28px #102a4330; transform:translateX(105%); transition:transform .18s ease;
      z-index:20; padding:20px; overflow:auto; }}
    #evidence-drawer.open {{ transform:translateX(0); }}
    #evidence-drawer header {{ background:white; color:#17202a; padding:0 0 16px; }}
    #evidence-close {{ margin-left:auto; background:#627d98; }}
    #evidence-content section {{ border-top:1px solid #d9e2ec; padding:12px 0; }}
    #evidence-content pre {{ white-space:pre-wrap; overflow-wrap:anywhere; background:#f3f6f8; padding:10px; }}
    @media (max-width: 720px) {{ #evidence-drawer {{ width:auto; left:0; top:18vh; border-radius:16px 16px 0 0; }} }}
  </style>
</head>
<body>
  <header>
    <strong>Enterprise Database Copilot</strong>
    <span id="status">未登录</span>
    <button id="logout">退出</button>
  </header>
  <main>
    <form id="login">
      <h2>登录演示环境</h2>
      <input id="username" value="analyst" autocomplete="username" aria-label="用户名" />
      <input id="password" type="password" value="analyst123" autocomplete="current-password" aria-label="密码" />
      <button type="submit">登录</button>
      <p id="error" role="alert"></p>
    </form>
    <vanna-chat id="chat"></vanna-chat>
  </main>
  <aside id="evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="evidence-title">
    <header><strong id="evidence-title">答案依据</strong><button id="evidence-close" aria-label="关闭答案依据">关闭</button></header>
    <div id="evidence-content">本次请求未启用 v2 证据链</div>
  </aside>
  <script>
    const loginUrl = {login_base} + "/api/auth/login";
    const form = document.querySelector('#login');
    const chat = document.querySelector('#chat');
    const status = document.querySelector('#status');
    const logout = document.querySelector('#logout');
    const error = document.querySelector('#error');
    const drawer = document.querySelector('#evidence-drawer');
    const evidenceContent = document.querySelector('#evidence-content');
    const evidenceClose = document.querySelector('#evidence-close');

    function closeEvidence() {{ drawer.classList.remove('open'); }}
    evidenceClose.addEventListener('click', closeEvidence);
    document.addEventListener('keydown', (event) => {{ if (event.key === 'Escape') closeEvidence(); }});

    function renderEvidence(view) {{
      if (!view.available) return '<p>本次请求未启用 v2 证据链</p>';
      const plan = view.query_plan || {{}};
      const assets = view.assets || {{}};
      const validation = view.validation || {{}};
      const safe = (value) => JSON.stringify(value ?? [], null, 2)
        .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
      return `<section><h3>查询含义</h3><pre>${{safe(plan)}}</pre></section>
        <section><h3>数据依据</h3><pre>${{safe(assets)}}</pre></section>
        <section><h3>可信状态</h3><pre>${{safe(validation)}}</pre></section>
        <section><h3>运行信息</h3><pre>${{safe(view.run)}}</pre></section>`;
    }}

    chat.addEventListener('artifact-opened', async (event) => {{
      event.preventDefault();
      const artifact = event.detail?.artifact || event.detail?.rich_component || event.detail || {{}};
      let artifactPayload = artifact;
      if (typeof artifact.content === 'string') {{
        try {{ artifactPayload = JSON.parse(artifact.content); }} catch (_) {{ artifactPayload = {{}}; }}
      }}
      const runId = artifactPayload.run_id;
      if (!runId) return;
      drawer.classList.add('open');
      evidenceContent.textContent = '正在加载答案依据';
      const token = sessionStorage.getItem('copilot_token');
      const response = await fetch(`/api/runs/${{encodeURIComponent(runId)}}/evidence`, {{
        headers: {{ Authorization: `Bearer ${{token}}` }}
      }});
      if (response.status === 401 || response.status === 403 || response.status === 404) {{
        evidenceContent.textContent = '无权查看此运行的依据'; return;
      }}
      if (!response.ok) {{ evidenceContent.textContent = '依据版本已过期或暂不可用'; return; }}
      evidenceContent.innerHTML = renderEvidence(await response.json());
    }});

    function installAuthenticatedFetch() {{
      if (window.__copilotAuthenticatedFetchInstalled) return;
      window.__copilotAuthenticatedFetchInstalled = true;
      const nativeFetch = window.fetch.bind(window);
      window.fetch = (input, init = {{}}) => {{
        const url = typeof input === 'string' ? input : input?.url || '';
        if (url.includes('/api/vanna/v2/chat_sse')) {{
          const token = sessionStorage.getItem('copilot_token');
          if (token) {{
            const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
            headers.set('Authorization', `Bearer ${{token}}`);
            return nativeFetch(input, {{ ...init, headers }});
          }}
        }}
        return nativeFetch(input, init);
      }};
    }}

    async function showChat(token, role) {{
      await customElements.whenDefined('vanna-chat');
      installAuthenticatedFetch();
      chat.setCustomHeaders({{ Authorization: `Bearer ${{token}}` }});
      chat.setAttribute('sse-endpoint', '/api/vanna/v2/chat_sse');
      form.style.display = 'none';
      chat.style.display = 'block';
      logout.style.display = 'inline-block';
      status.textContent = `已登录：${{role}}`;
    }}

    form.addEventListener('submit', async (event) => {{
      event.preventDefault();
      error.textContent = '';
      const response = await fetch(loginUrl, {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{
          username: document.querySelector('#username').value,
          password: document.querySelector('#password').value
        }})
      }});
      if (!response.ok) {{ error.textContent = '登录失败'; return; }}
      const data = await response.json();
      sessionStorage.setItem('copilot_token', data.accessToken);
      sessionStorage.setItem('copilot_role', data.role);
      await showChat(data.accessToken, data.role);
    }});

    logout.addEventListener('click', () => {{
      sessionStorage.clear();
      location.reload();
    }});

    const existingToken = sessionStorage.getItem('copilot_token');
    if (existingToken) showChat(existingToken, sessionStorage.getItem('copilot_role') || 'unknown');
  </script>
</body>
</html>"""
