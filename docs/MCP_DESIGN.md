# Overstone MCP Connector: Design

**Status:** proposal for review, not built · **Date:** 2026-10-08

Lets Overstone's clients use their own AI assistant (Claude and other MCP-capable tools) to ask about
their collections: assets, valuations, documents. It reuses the platform's existing logins and permissions,
and the AI search we already run (Overstone AI).

## 1. What the client experiences

1. **One-time setup:** the user adds the "Overstone" connector in their AI assistant and **signs in with
   their normal Overstone login**.
2. **After that:** they just ask questions. The assistant decides by itself when to call Overstone, for example:
   - "What's the insured value of my Giacometti?"
   - "Which of my works were bought at Christie's? Send me the invoices."
   - "Summarise the provenance of Gaillard."
3. **What they notice:** a small "using Overstone" indicator in the chat, a permission prompt the first time a
   tool is used, and an occasional sign-in again when the session expires.

They only ever see **what they can already see in the Overstone platform**.

## 2. Scope of the first version

- **Read-only.** No creating, editing or deleting.
- **Logged-in platform users only,** with their existing roles and permissions.
- **Pilot first:** Overstone staff on their own accounts, then one friendly client (see section 8).

## 3. How it works

```
 Client's AI assistant (e.g. Claude)
        │  1. user signs in (Overstone login)
        │  2. calls a tool, with a token issued for the MCP server
        ▼
 Overstone MCP server  (new, public over HTTPS, behind sign-in)
        │  3. checks the token, identifies the user
        │  4. calls the platform as itself, "on behalf of user X"
        ▼
 Platform server (existing, Elastic Beanstalk)
        │  5. applies user X's permissions (account, collections, role)
        ├──► MySQL: assets, valuations, files
        ├──► signed PDF links (CloudFront)
        └──► Overstone AI search (the RAG Lambda)
```

**Key decision: the MCP server is a thin layer over the platform server,** not a new path to the
database or ChromaDB. All permission rules stay in one place (the platform server), so the connector can
never show more than the web app does. This also fixes a gap in today's AI search, which searches a whole
account rather than only what the user may see.

## 4. Tools

| Tool | What it does | Built on |
|---|---|---|
| `search_documents(question)` | Cited answers, or assets ranked by what their documents say | `askAssetsAI` (Overstone AI) |
| `list_assets(type?, artist?, year_from?, year_to?, …)` | Find assets by their details | existing `getAssets` |
| `find_assets_by_name(name)` | Find assets by (part of) their name, e.g. "Silver Shell"; returns a short list with IDs, since a name can match several assets | **new** server query |
| `get_asset(asset_id)` | Full details of one asset: valuations, location, documents list. The assistant uses the ID from `find_assets_by_name` or `list_assets`; users never type IDs | existing `getAssetById` |
| `get_document_link(file_id, page?)` | A time-limited link to a PDF, opening at a page | existing signed-URL query |
| `asset_stats(group_by)` | Counts and totals, calculated by the database | **new** server query |

Users refer to assets by name ("my Giacometti"), so `find_assets_by_name` is the usual entry point; the
assistant chains the tools by itself (find → get details → get document link).

Tool names and descriptions matter: they're how the assistant decides **when** to call Overstone, so
they must say clearly what each covers ("documents attached to the user's Overstone assets: invoices,
valuations, provenance, certificates, insurance…").

## 5. Sign-in and permissions

**What the MCP rules require** (specification 2026-07-28):
- Sign-in uses **OAuth 2.1**. The MCP server publishes where to sign in (its "protected resource metadata").
- Every request carries a token that the MCP server **must check was issued for the MCP server itself**.
- The MCP server **must not forward that token** to other services ("token passthrough" is forbidden).
  It calls the platform server with its **own** credentials, and says which user it's acting for.

**How it maps to Overstone:**
- **Sign-in:** the platform's existing **AWS Cognito** user pool, so users keep their usual login.
- **Who the user is:** taken from the verified token (Cognito user), never from what the assistant sends.
- **Platform call:** the MCP server authenticates to the platform server as a trusted service, plus the user's
  identity. The platform server needs a small addition to accept this, and must apply that user's
  permissions exactly as for a web request.

**Open point, client registration:** MCP clients prefer to register themselves automatically (Client ID
Metadata Documents, or the older Dynamic Client Registration). **Cognito supports neither.** Options:
1. **Pre-register** the connector in Cognito and give clients the client ID to enter in their assistant
   (simple; needs checking that the target assistants support it).
2. A small **OAuth adapter** in front of Cognito (our own, or open source) that offers automatic registration.
3. AWS Bedrock AgentCore Identity, if we later move to managed hosting.

## 6. Where it runs

The MCP server must be reachable **from the internet over HTTPS**: assistants connect from their
provider's cloud, not from the user's laptop. Only the MCP layer is public; the platform server, database
and ChromaDB stay private.

**Decision: AWS Lambda behind API Gateway** (HTTPS), deployed with CDK like the query Lambda.
- Matches the rest of the project, and costs nothing when idle.
- The MCP server runs statelessly over HTTP (each request stands alone), which fits Lambda.
- We build the sign-in handling ourselves (publishing the protected resource metadata, checking tokens),
  which is also a way to learn MCP end to end.

Alternatives considered: Bedrock AgentCore Gateway (managed sign-in, newer service) and ECS Fargate / App
Runner (always-on containers, small monthly cost).

## 7. Data protection and legal

- **Client data goes to the client's AI provider.** Each client's agreement (and data processing terms)
  must allow it. This is the biggest non-technical question.
- **Mask sensitive numbers:** turn on the guardrail's PII masking (bank and card details) and mask the
  citation text too, before any external pilot.
- **Audit log:** record who called which tool, when, for which account. Often required by contracts.
- **Rate limits** per user and client, and a way to **switch off** a client's access immediately.
- Data location (EU processing) and logging retention as in the planned privacy document.

## 8. Phases

| Phase | What | Exit criteria |
|---|---|---|
| **1. Internal pilot** | 5 read-only tools, Overstone staff on their own accounts | Sign-in works in the target assistants; answers match the web app |
| **2. External pilot** | One friendly client, read-only, PII masking on, audit log | Signed agreement; no permission leaks in testing |
| **3. General availability** | `asset_stats`, documentation, monitoring, rate limits | Support process in place |

## 9. Open questions

1. **Registration:** pre-registered client vs an OAuth adapter (section 5). Verify what the
   target assistants (Claude Desktop, claude.ai, others) support.
2. **Which assistants** do pilot clients use?
3. **Platform server change:** how exactly it accepts "service + user" calls (a new internal endpoint, or a
   signed header on the existing GraphQL API).
4. **Legal:** client agreement wording for sending data to their AI provider.
5. **Write actions:** wanted later (e.g. upload a document, add a note)? Each needs its own review.

---

Related: [ARCHITECTURE_AND_DEPLOYMENT.md](ARCHITECTURE_AND_DEPLOYMENT.md) (current system, security gaps),
[HYBRID_QUERY_STRATEGY.md](HYBRID_QUERY_STRATEGY.md) (how the AI search answers),
[RESPONSE_CONTRACT.md](RESPONSE_CONTRACT.md) (what `search_documents` returns).
