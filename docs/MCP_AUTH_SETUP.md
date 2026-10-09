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

## Next steps

1. ~~Run command 1.~~ Done.
2. ~~Choose the adapter (check MCP libraries).~~ FastMCP; added with `uv add fastmcp` (4.1.0, alongside `mcp` 2.3.0,
   unit tests pass). Server switched from `MCPServer` to `FastMCP` (no sign-in yet); same answer as before.
3. Run the MCP server locally with the adapter; create the app client with a localhost return address.
4. Test with Claude and one other client (VS Code or Cursor).
5. Branding (logo, brand colour) for the login page.
