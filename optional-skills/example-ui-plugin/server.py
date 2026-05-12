from __future__ import annotations

import os
from datetime import UTC, datetime

from aiohttp import web


def now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


async def index(_: web.Request) -> web.Response:
    html = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Example Clock</title>
    <style>
      :root { color-scheme: light; font-family: Inter, system-ui, sans-serif; }
      body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #fffaf1; color: #1e2430; }
      main { width: min(100%, 26rem); padding: 1.2rem; text-align: center; }
      .label { margin: 0 0 0.35rem; color: #5f6b76; font-size: 0.78rem; font-weight: 700; text-transform: uppercase; }
      .clock { font-size: clamp(2rem, 10vw, 4.8rem); font-weight: 800; line-height: 1; color: #0d766e; }
      .iso { margin-top: 0.8rem; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.82rem; color: #5f6b76; overflow-wrap: anywhere; }
    </style>
  </head>
  <body>
    <main>
      <p class="label">Example Clock</p>
      <div class="clock" id="clock"></div>
      <div class="iso" id="iso"></div>
    </main>
    <script>
      async function tick() {
        const response = await fetch('api/time', { cache: 'no-store' });
        const payload = await response.json();
        const dt = new Date(payload.now);
        document.getElementById('clock').textContent = dt.toLocaleTimeString();
        document.getElementById('iso').textContent = payload.now;
      }
      tick();
      setInterval(tick, 1000);
    </script>
  </body>
</html>"""
    return web.Response(text=html, content_type="text/html")


async def api_time(_: web.Request) -> web.Response:
    return web.json_response({"now": now_iso()})


def main() -> None:
    port = int(os.environ["OPEN_STRIX_PORT"])
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/time", api_time)
    web.run_app(app, host="127.0.0.1", port=port, print=None)


if __name__ == "__main__":
    main()
