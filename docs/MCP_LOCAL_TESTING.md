# MCP server: local testing

How to run and test the Overstone MCP server ([mcp_server/server.py](../mcp_server/server.py)) on your machine.
Sign-in setup: [MCP_AUTH_SETUP.md](MCP_AUTH_SETUP.md).

## MCP Inspector (browser UI)

The MCP Inspector is a web page for testing an MCP server by hand: list its tools, call them, and see the raw
replies. FastMCP starts it for you. It needs Node.js (`npx`).

```bash
uv run poe inspector
```

which runs:

```bash
uv run fastmcp dev inspector -m mcp_server.server --reload-dir mcp_server --reload-dir app
```

Then:
1. Keep the terminal open: the Inspector runs only while the command runs (Ctrl+C stops it).
2. Use the browser tab it opens. If no tab opens, copy the full link from the terminal
   (`http://127.0.0.1:6274?MCP_INSPECTOR_API_TOKEN=...`). The token is new on every start, so the plain
   `http://127.0.0.1:6274` doesn't work, and the link stops working once the Inspector is stopped.
3. Click **Connect**. Leave the connection settings as filled in (Transport STDIO, command `uv`,
   arguments `run --directory <repo> python -m mcp_server.server`).
4. **Tools** → **List Tools** → `search_documents` → type a question → **Run Tool**.

### Why each option

| Option | Problem it solves |
|---|---|
| `-m mcp_server.server` (instead of the file path `mcp_server/server.py`) | With a file path, FastMCP loads the file on its own, so `from app.config import config` fails with `ModuleNotFoundError: No module named 'app'`. `-m` loads it as a module from the repo root, where `app/` is found. |
| `--reload-dir mcp_server --reload-dir app` | Auto-reload (on by default) watches the whole repo, including `minio-data/`, which belongs to the MinIO container and can't be read. The watcher crashes with `Permission denied ... minio-data/.minio.sys/...` and the connection drops after about 2 seconds. Watching only the code folders avoids it and still reloads on code changes. |
| `--no-reload` (alternative) | Turns auto-reload off completely; restart the command after code changes. |

### Messages you can ignore

`Keychain error: ... org.freedesktop.secrets was not provided`: the Inspector tries to save its settings in a
password store that WSL doesn't have, and falls back to a file (`~/.mcp-inspector/secrets.json`).

## Other ways to run it

| Command | What it does |
|---|---|
| `uv run poe mcp` | Runs the server over stdio. Normally an AI assistant (e.g. Claude Code) starts it, not you. |
| Claude Code `overstone` MCP connection | Claude Code starts its own copy of the server; its process (`uv run --directory ... python -m mcp_server.server`) is not the Inspector's. |
