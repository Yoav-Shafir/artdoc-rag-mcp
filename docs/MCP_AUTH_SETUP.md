# MCP connector: sign-in setup log

A running record of the decisions and commands for signing users in to the Overstone MCP connector.
Design background: [MCP_DESIGN.md](MCP_DESIGN.md) (section 5, sign-in and permissions).
Running and testing locally (MCP Inspector): [MCP_LOCAL_TESTING.md](MCP_LOCAL_TESTING.md).

## Decisions

| Date | Decision | Why |
|---|---|---|
| 2026-10-09 | Sign in with the platform's **existing Cognito pool**, not a new pool | Users keep their usual login, and the signed-in user is already the platform user. A new pool would mean second accounts and our own mapping to platform accounts. |
| 2026-10-09 | Start in the **dev pool** `web-platform-stg` (`eu-west-1_UTwJgz6im`, `eu-west-1`) | Safe for testing (about 2 users). Production gets the same additions later. |
| 2026-10-09 | Use Cognito's **built-in login page (Managed Login)**, branded, not the platform's custom login page | No changes to the platform front end. The custom page always redirects to the dashboard after login. |
| 2026-10-09 | Must work with **any AI client** (Claude, ChatGPT, Cursor, VS Code…), not only Claude | Clients register themselves automatically (Dynamic Client Registration or Client ID Metadata Documents), and Cognito supports neither, so an **OAuth adapter** sits in front of Cognito. |
| 2026-10-09 | Look for a ready-made adapter (e.g. FastMCP's OAuth proxy with a Cognito option) before building one | Less code; it can run inside the MCP server. Not yet decided. |
| 2026-10-09 | Adapter = **FastMCP `AWSCognitoProvider`** (see comparison below) | Most widely used Python MCP framework, documented Cognito support, offers DCR to any client, least code to maintain. |
| 2026-10-09 | Local server port **8080**, not 8000 | 8000 is taken by ChromaDB (Docker). Cognito return address changed to `http://localhost:8080/auth/callback` (command 3). |
| 2026-10-09 | Sign-in only in http mode (`uv run poe mcp-http`); stdio (`uv run poe mcp`) stays without sign-in | An assistant on the same machine starts the stdio server directly; sign-in is for remote (http) clients. |
| 2026-10-09 | Pool name stays `web-platform-stg` | Cognito pool names can't be changed, and users never see them. The domain and branding are what users see. |

## Adapter options compared (2026-10-09, docs only, pending decision)

| Option | What it is | For | Against |
|---|---|---|---|
| **FastMCP `AWSCognitoProvider`** (recommended) | Built-in Cognito provider of FastMCP, the most widely used Python MCP framework (PrefectHQ). Its OAuth proxy offers automatic registration to any client and maps them all onto one pre-registered Cognito client. | A few lines of config; maintained by a large project; documented Cognito integration. | Replace the official SDK's `MCPServer` with `FastMCP` (small change). On Lambda it needs persistent storage for registered clients and tokens (e.g. Redis or DynamoDB), encrypted, plus a signing key. |
| Official MCP Python SDK | What `mcp_server/server.py` uses today. Has sign-in building blocks. | No new dependency. | We'd write and maintain the registration and Cognito redirect logic ourselves. |
| AWS sample `sample-serverless-architecture-cimd-with-cognito` | AWS reference architecture: Lambdas, API Gateway, DynamoDB, Cognito. | AWS-native. | Sample code (not a product); Client ID Metadata Documents only, no DCR; localhost clients (desktop/CLI) out of scope; several extra services. |
| Small open-source proxies (e.g. `authsome-mcp-proxy`) | Separate proxy in front of the MCP server. | Ready-made. | Small projects, separate service to run. |

Notes: some clients (e.g. OpenAI Codex) require DCR, so CIMD-only is not enough for "any client".
With FastMCP the Cognito return address is `<base_url>/auth/callback` (local: `http://localhost:8000/auth/callback`).

## Current state of the dev pool (checked 2026-10-09, read-only)

| Item | Value |
|---|---|
| Domain | none |
| Login triggers (Lambda) | none |
| Tier | Plus (Managed Login branding included) |
| MFA | optional |
| App clients | `OverstoneWpAppStg` (the web app; direct login with SRP, no OAuth settings) |

The web app's client has no OAuth settings, so the additions below don't affect it.

## Facts found

- claude.ai connectors support automatic registration (DCR) and also a manually entered client ID and
  secret. Claude's return address is `https://claude.ai/api/mcp/auth_callback` (may move to `claude.com`).
  Source: third-party connector guides; the official Claude help page returned 404 when checked.
- The AWS CLI profile is `overstone` (lower case).

## Commands

Claude Code's permission rules block it from changing the Cognito pool, so these are run by hand.

### 1. Login domain — done 2026-10-09 (checked: ACTIVE, managed login version 2)

Gives the pool a web address for its built-in login page:
`https://overstone-stg.auth.eu-west-1.amazoncognito.com`. AWS hosts it, so no Route 53 or certificate is needed.
`--managed-login-version 2` selects the newer, brandable login page.

```bash
aws cognito-idp create-user-pool-domain --domain overstone-stg \
  --user-pool-id eu-west-1_UTwJgz6im --managed-login-version 2 \
  --profile overstone --region eu-west-1
```

Revert: `aws cognito-idp delete-user-pool-domain --domain overstone-stg --user-pool-id eu-west-1_UTwJgz6im --profile overstone --region eu-west-1`

### 2. App client for the adapter — done 2026-10-09 (client ID `7ldjat3p2a6fd5kj5c05s3sh4n`, settings checked)

The return address is FastMCP's callback on the local server. Add the hosted address later
(`aws cognito-idp update-user-pool-client`; note it resets any setting not passed, so pass them all again).

The options are in a list so each can have its own comment; paste the whole block into the terminal.

```bash
args=(
  # The dev pool to add the app to
  --user-pool-id eu-west-1_UTwJgz6im
  # Name shown in the AWS console
  --client-name OverstoneMcpConnectorStg
  # Creates a password for the adapter, so only our server can use this app
  --generate-secret
  # Standard sign-in: user logs in on the Cognito page, the server gets a one-time code
  # and swaps it for the user's tokens
  --allowed-o-auth-flows code
  # Turns on the OAuth sign-in settings above for this app
  --allowed-o-auth-flows-user-pool-client
  # What the server may learn about the user: their ID, email and name
  --allowed-o-auth-scopes openid email profile
  # Users log in with their normal Overstone username and password (no Google etc.)
  --supported-identity-providers COGNITO
  # The only address Cognito may send users back to after login (the local MCP server)
  --callback-urls http://localhost:8000/auth/callback
  # Lets the server renew the login quietly, so users don't sign in again every hour
  --explicit-auth-flows ALLOW_REFRESH_TOKEN_AUTH
  # On a failed login, don't reveal whether the email exists
  --prevent-user-existence-errors ENABLED
  # The AWS profile and region to use
  --profile overstone --region eu-west-1
)
aws cognito-idp create-user-pool-client "${args[@]}"
```

Keep the printed client secret private (never in chat or git). Put both values in `.env` as
`COGNITO_CLIENT_ID` and `COGNITO_CLIENT_SECRET`.

Revert: `aws cognito-idp delete-user-pool-client --user-pool-id eu-west-1_UTwJgz6im --client-id <ClientId> --profile overstone --region eu-west-1`

### 3. Return address on port 8080 — done 2026-10-09 (checked: only callback changed)

Port 8000 is ChromaDB's, so the local MCP server runs on 8080. `update-user-pool-client` replaces the
whole app configuration, so every setting from command 2 is passed again; only the return address changes.

```bash
args=(
  # The dev pool and the MCP connector app (client ID from command 2)
  --user-pool-id eu-west-1_UTwJgz6im
  --client-id 7ldjat3p2a6fd5kj5c05s3sh4n
  # Same settings as command 2 (an update resets anything not passed)
  --client-name OverstoneMcpConnectorStg
  --allowed-o-auth-flows code
  --allowed-o-auth-flows-user-pool-client
  --allowed-o-auth-scopes openid email profile
  --supported-identity-providers COGNITO
  # The only change: the local MCP server's return address, now on port 8080
  --callback-urls http://localhost:8080/auth/callback
  --explicit-auth-flows ALLOW_REFRESH_TOKEN_AUTH
  --prevent-user-existence-errors ENABLED
  # The AWS profile and region to use
  --profile overstone --region eu-west-1
)
aws cognito-idp update-user-pool-client "${args[@]}"
```

The client secret doesn't change.

### 4. Login page style for the app — done 2026-10-09 (ID `8363afa5-5b4c-4172-912b-43f743e472ff`, Cognito default style)

The new login page (managed login version 2) shows "Login pages unavailable. Please contact an
administrator." until the app has a style. This gives it Cognito's default style; the Overstone logo and
colours can replace it later (step 5 of next steps).

```bash
args=(
  # The dev pool and the MCP connector app
  --user-pool-id eu-west-1_UTwJgz6im
  --client-id 7ldjat3p2a6fd5kj5c05s3sh4n
  # Use Cognito's default look for now
  --use-cognito-provided-values
  # The AWS profile and region to use
  --profile overstone --region eu-west-1
)
aws cognito-idp create-managed-login-branding "${args[@]}"
```

Revert: `aws cognito-idp delete-managed-login-branding --user-pool-id eu-west-1_UTwJgz6im --managed-login-branding-id <id from the output> --profile overstone --region eu-west-1`

### 5. Test user — done 2026-10-09 (status CONFIRMED)

The pool signs in with email and requires `email`, `name` and `phone_number`. Passwords: at least 8
characters with upper case, lower case, a number and a symbol. Set the email first, then run the block;
it asks for the password without showing it or saving it in shell history.

```bash
TEST_EMAIL="<your test email>"
args=(
  # The dev pool
  --user-pool-id eu-west-1_UTwJgz6im
  # Sign-in name (the pool uses email as the username)
  --username "$TEST_EMAIL"
  # Required fields; email marked verified so login works without a confirmation email
  --user-attributes Name=email,Value="$TEST_EMAIL" Name=email_verified,Value=true Name=name,Value="MCP Test" Name=phone_number,Value=+440000000000
  # Don't send Cognito's invitation email
  --message-action SUPPRESS
  # The AWS profile and region to use
  --profile overstone --region eu-west-1
)
aws cognito-idp admin-create-user "${args[@]}"

# Set a permanent password (no "change password" step at first login)
read -rsp "Password: " TEST_PASSWORD; echo
aws cognito-idp admin-set-user-password --user-pool-id eu-west-1_UTwJgz6im \
  --username "$TEST_EMAIL" --password "$TEST_PASSWORD" --permanent \
  --profile overstone --region eu-west-1
unset TEST_PASSWORD
```

### 6. Test user in the platform's MySQL — done 2026-10-09 (user id 241, profile id 241)

Written to the dev MySQL with the user's OK, so the server can find the signed-in user's account.
Account 7 (not 46), so the test user should not see account 46's documents. `identifier` is the Cognito
user ID (`sub`), as for most platform users. User id 241 because profile rows 238–240 already exist
without a matching user (left as they were).

```sql
START TRANSACTION;
INSERT INTO user (id,identifier,email,name,mfa,roleId,accountId,enabled,authenticationStatus)
VALUES (241,'52653454-c051-70be-3401-4a0e92df664b','mcptest@gmail.com','MCP Test','NO_MFA',4,7,1,'CONFIRMED');
INSERT INTO profile (userId) VALUES (241);
COMMIT;
```

Revert: `DELETE FROM user WHERE id = 241;` (the profile row goes with it, `ON DELETE CASCADE`).

Revert: `aws cognito-idp admin-delete-user --user-pool-id eu-west-1_UTwJgz6im --username "$TEST_EMAIL" --profile overstone --region eu-west-1`

### 7. Test user moved to account 46 — done 2026-10-10

Written to the dev MySQL with the user's OK, so the test user can search account 46's documents
(e.g. "Half of You"); on account 7 every such search returned "No documents found."

```sql
UPDATE user SET accountId = 46 WHERE id = 241 AND accountId = 7;
```

Revert: `UPDATE user SET accountId = 7 WHERE id = 241;`

### 8. Overstone logo and colour on the login page — done 2026-10-10 (checked: logo on, button `b82c30ff`)

Updates the style from command 4. Logo: `web-platform-client-v2/src/assets/overstone_logo.png` (full logo),
shown at the top of the form. Red from the logo ring (`#b82c30`, hover `#8f2226`) for the button, links and
focus outline; light mode only (the page is light only). The request file was built from the current style
(`describe-managed-login-branding --return-merged-resources`) with only those values changed, plus the logo as
base64 in `Assets` (`Category FORM_LOGO, ColorMode LIGHT, Extension PNG`).

```bash
aws cognito-idp update-managed-login-branding --cli-input-json file://update.json \
  --profile overstone --region eu-west-1
```

Revert (back to Cognito's default look): `aws cognito-idp update-managed-login-branding --user-pool-id eu-west-1_UTwJgz6im --managed-login-branding-id 8363afa5-5b4c-4172-912b-43f743e472ff --use-cognito-provided-values --profile overstone --region eu-west-1`

## Code: sign-in in the MCP server (2026-10-09)

`mcp_server/server.py`, http mode only: `AWSCognitoProvider(user_pool_id, aws_region, client_id, client_secret,
base_url="http://localhost:8080")`, with `COGNITO_CLIENT_ID` / `COGNITO_CLIENT_SECRET` read from `.env`.
Checked with curl: `POST /mcp` without a token → `401` pointing at
`/.well-known/oauth-protected-resource/mcp`; the server offers `/register` (DCR) and Client ID Metadata
Documents. Still a fixed test account (46); the signed-in username is only logged.

## Problems hit and fixes (2026-10-09)

| Symptom | Cause | Fix |
|---|---|---|
| Server won't start: `address already in use` on 8000 | ChromaDB uses 8000 | Port 8080 (command 3) |
| Cognito page: "Login pages unavailable. Please contact an administrator." | Managed login v2 needs a style per app | Command 4 |
| After login: server log `invalid_grant`, `GET /auth/callback` → 500 | FastMCP forwards `resource=http://localhost:8080/mcp` to Cognito, which rejects the code for an unregistered resource | `forward_resource=False` in `AWSCognitoProvider` |
| No login form on a second try | Cognito remembers the browser's login for a while (session cookie) | Expected; use a private window to see the form again |

First successful sign-in from Claude Code (`overstone-http`): 2026-10-09. Connect steps in
[MCP_LOCAL_TESTING.md](MCP_LOCAL_TESTING.md#connect-claude-code-to-the-mcp-server-with-sign-in).
Test question "Who issued the invoice for Half of You?" → "Esthella Provas & Associates Art Advisory
Services", citing `YONA0004_2.pdf` page 1.

## Next steps

1. ~~Run command 1.~~ Done.
2. ~~Choose the adapter (check MCP libraries).~~ FastMCP; added with `uv add fastmcp` (4.1.0, alongside `mcp` 2.3.0,
   unit tests pass). Server switched from `MCPServer` to `FastMCP` (no sign-in yet); same answer as before.
3. ~~Run the MCP server locally with the adapter; create the app client with a localhost return address.~~
   Done (`uv run poe mcp-http`, port 8080, command 3 run).
4. ~~Test with Claude and one other client (VS Code or Cursor).~~ Done 2026-10-10 with `mcptest@gmail.com`:
   on account 7 the Half of You question returned "No documents found.", on account 46 the right answer
   (command 7), so each signed-in user only sees their own account.
5. ~~Branding (logo, brand colour) for the login page.~~ Done (command 8).
