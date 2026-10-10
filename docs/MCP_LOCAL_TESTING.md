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
| `uv run poe mcp` | Runs the server over stdio, no sign-in. Normally an AI assistant (e.g. Claude Code) starts it, not you. |
| `uv run poe mcp-http` | Runs the server at `http://localhost:8080/mcp` with Cognito sign-in. Port 8080 because ChromaDB uses 8000. |
| `uv run poe mcp-dev` | Starts Docker (MinIO, ChromaDB) and the platform's MySQL, then the same server as `mcp-http`. Use it when those aren't running; the server needs MySQL to find the signed-in user's account. |
| Claude Code `overstone` MCP connection | Claude Code starts its own copy of the server; its process (`uv run --directory ... python -m mcp_server.server`) is not the Inspector's. |

## Connect Claude Code to the MCP server (with sign-in)

```bash
uv run poe mcp-dev                                                           # terminal 1: start Docker, MySQL and the server
claude mcp add --transport http overstone-http http://localhost:8080/mcp     # once; saved for this project only
```
In Claude Code, run `/mcp`, pick `overstone-http` and sign in on the Cognito page that opens.
`claude mcp get overstone-http` should then show `Connected`.

### Start over with a fresh sign-in

Sign-ins are remembered in three places, so reconnecting usually skips the login page and signs in the
same user again. To sign in as a different user, clear all three, in this order.

**1. The MCP server**

1.1. Stop the server: Ctrl+C in its terminal.

1.2. Delete the sign-in data the server saved on disk: the apps that connected to it and their login
tokens, in `~/.local/share/fastmcp/oauth-proxy`:

```bash
uv run poe mcp-reset
```

**2. Claude Code**

2.1. In the Claude Code chat: `/mcp` → `overstone-http` → **Clear authentication**.

2.2. This doesn't always remove the saved token, so check (it should print nothing):

```bash
jq '.mcpOAuth | keys[] | select(startswith("overstone-http"))' ~/.claude/.credentials.json
```

If it printed nothing, go to step 3. If it printed a key:

2.3. Close any other Claude Code sessions (they may write the token back).

2.4. Remove the key, keeping a backup:

```bash
cp ~/.claude/.credentials.json ~/.claude/.credentials.json.bak
jq '.mcpOAuth |= with_entries(select(.key | startswith("overstone-http") | not))' ~/.claude/.credentials.json.bak > ~/.claude/.credentials.json
```

Undo: `cp ~/.claude/.credentials.json.bak ~/.claude/.credentials.json`

2.5. Reload VS Code (**Developer: Reload Window**): a running Claude Code keeps the token in memory.

**3. The browser**

Cognito keeps its own login cookie and signs the last user in without showing the page.

3.1. In Chrome open `chrome://settings/content/all`.

3.2. Search `amazoncognito` and click **Delete displayed data**. (The `localhost` cookies are other local
apps, not Cognito.) Alternative: copy the login link from Claude Code into a private window in step 4.2.

**4. Sign in**

4.1. Start Docker, the platform's MySQL and the server:

```bash
uv run poe mcp-dev
```

4.2. In the Claude Code chat: `/mcp` → `overstone-http` → **Authenticate**, and log in on the Cognito page.

4.3. `/mcp` should show `overstone-http` as connected.

**5. Check who is signed in**

5.1. Ask Claude a question about the documents, for example:
"Run search_documents on overstone-http: Who issued the invoice for Half of You?"

5.2. In the server terminal, find the line with the user and account it searched:

```
search_documents by <user sub> (account <id>): <question>
```

A signed-in user only searches their own platform account (never the test account 46), so "No documents
found." can just mean the documents belong to another account.
