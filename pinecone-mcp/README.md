# Pinecone MCP connector for Claude

A remote MCP server that lets Claude (web, desktop, mobile, and voice mode) search your Pinecone database.

## Tools

| Tool | What it does |
|---|---|
| `list_indexes` | Lists your indexes |
| `describe_index` | Record counts per namespace |
| `search` | Natural-language semantic search |
| `fetch` | Get records by ID |
| `upsert_text` | Save text (only when `ALLOW_WRITES=true`) |
| `delete_records` | Delete by ID (only when `ALLOW_WRITES=true`) |

Results are kept short (each hit is cut to 1,500 characters), so they work in voice mode too.

## Configure

| Variable | Required | Notes |
|---|---|---|
| `PINECONE_API_KEY` | yes | From app.pinecone.io → API Keys |
| `MCP_SECRET` | yes | 24+ random chars (`openssl rand -hex 24`). It becomes part of the URL, so treat the URL like a password. |
| `DEFAULT_INDEX` | no | Index used when Claude doesn't name one |
| `DEFAULT_NAMESPACE` | no | |
| `EMBED_MODEL` | no | Leave empty if your index uses **integrated embedding** (created with a model such as `llama-text-embed-v2`). If your index stores raw vectors, set the Pinecone-hosted model that produced them, e.g. `multilingual-e5-large`. |
| `TEXT_FIELD` | no | Field that holds the document text (default `text`; `chunk_text` and `content` are also detected) |
| `ALLOW_WRITES` | no | `true` turns on the upsert and delete tools |

## Deploy

Claude connects to the server from Anthropic's cloud, so it has to be reachable on a public HTTPS URL.

**Render (easiest):** this folder lives in the `Claude-Personal-Assistant` repo, and the repo root has a `render.yaml` pointing at it. In Render choose New → Blueprint, pick `kylechen839-max/Claude-Personal-Assistant`, and enter `PINECONE_API_KEY` when asked (`DEFAULT_INDEX` is preset to `project-context`, the index the voice assistant uses). Render generates `MCP_SECRET`; copy it from the service's Environment tab. The Blueprint uses Render's free plan, which sleeps after about 15 minutes idle, so the first call after a pause takes about a minute; switch the service to a paid plan if that is too slow.

**Any Docker host** (Fly.io, Railway, Cloud Run, a VPS): `docker build -t pinecone-mcp .` and run it with the environment variables above.

**Local test:** `npm install && cp .env.example .env` (fill it in), then `node --env-file=.env --import tsx src/server.ts`.

## Add to Claude

1. Go to claude.ai → Settings → Connectors → **Add custom connector**.
2. Name: `Pinecone`. URL: `https://<your-host>/mcp/<MCP_SECRET>`. Leave the OAuth fields empty.
3. In a chat, enable it from the tools/connectors menu. Connectors added on claude.ai also work in the desktop and mobile apps and in voice mode.

Ask something like *"Search my Pinecone knowledge base for our refund policy."*

## Security

- The secret URL is the only credential. If it leaks, change `MCP_SECRET` and update the connector.
- Writes are off by default.
- Wrong paths return 404, and the secret is compared in constant time.
