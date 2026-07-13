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
    <vanna-chat id="chat" sse-endpoint="/api/vanna/v2/chat_sse"></vanna-chat>
  </main>
  <script>
    const loginUrl = {login_base} + "/api/auth/login";
    const form = document.querySelector('#login');
    const chat = document.querySelector('#chat');
    const status = document.querySelector('#status');
    const logout = document.querySelector('#logout');
    const error = document.querySelector('#error');

    async function showChat(token, role) {{
      await customElements.whenDefined('vanna-chat');
      chat.setCustomHeaders({{ Authorization: `Bearer ${{token}}` }});
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
