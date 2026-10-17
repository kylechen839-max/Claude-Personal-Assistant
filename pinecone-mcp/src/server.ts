import express from "express";
import { randomUUID, timingSafeEqual } from "node:crypto";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { Pinecone } from "@pinecone-database/pinecone";
import { z } from "zod";

const {
  PINECONE_API_KEY,
  MCP_SECRET, // random string; becomes part of the connector URL
  DEFAULT_INDEX, // optional: index used when a tool call doesn't name one
  DEFAULT_NAMESPACE = "", // optional
  EMBED_MODEL, // optional: set only if your index does NOT use integrated embedding
  TEXT_FIELD = "text", // field holding the document text
  ALLOW_WRITES = "false", // "true" to expose upsert/delete tools
  PORT = "3000",
} = process.env;

if (!PINECONE_API_KEY) throw new Error("PINECONE_API_KEY is required");
if (!MCP_SECRET || MCP_SECRET.length < 24)
  throw new Error("MCP_SECRET is required (at least 24 random characters)");

const pc = new Pinecone({ apiKey: PINECONE_API_KEY });
const writesEnabled = ALLOW_WRITES === "true";
const MAX_TEXT = 1500; // keep results short enough for voice + chat

const text = (s: string) => ({ content: [{ type: "text" as const, text: s }] });
const truncate = (s: unknown) => {
  const str = typeof s === "string" ? s : JSON.stringify(s);
  return str.length > MAX_TEXT ? str.slice(0, MAX_TEXT) + "…" : str;
};

function resolveIndex(name?: string) {
  const index = name ?? DEFAULT_INDEX;
  if (!index) throw new Error("No index given and DEFAULT_INDEX is not set. Call list_indexes first.");
  return index;
}

function safe<A>(fn: (args: A) => Promise<ReturnType<typeof text>>) {
  return async (args: A) => {
    try {
      return await fn(args);
    } catch (e) {
      return { ...text(`Error: ${(e as Error).message}`), isError: true };
    }
  };
}

const indexArg = z.string().optional().describe("Index name. Omit to use the default index.");
const nsArg = z.string().optional().describe("Namespace. Omit to use the default namespace.");

function buildServer() {
  const server = new McpServer({ name: "pinecone", version: "1.0.0" });

  server.registerTool(
    "list_indexes",
    {
      title: "List Pinecone indexes",
      description: "List the Pinecone indexes available, with dimension and record counts.",
      inputSchema: {},
      annotations: { readOnlyHint: true },
    },
    safe(async () => {
      const { indexes = [] } = await pc.listIndexes();
      if (!indexes.length) return text("No indexes found.");
      const lines = indexes.map((i) => `- ${i.name}${i.name === DEFAULT_INDEX ? " (default)" : ""}`);
      return text(`Indexes:\n${lines.join("\n")}`);
    }),
  );

  server.registerTool(
    "describe_index",
    {
      title: "Describe index",
      description: "Show record counts per namespace and dimension for an index.",
      inputSchema: { index: indexArg },
      annotations: { readOnlyHint: true },
    },
    safe(async ({ index }: { index?: string }) => {
      const name = resolveIndex(index);
      const stats = await pc.index({ name }).describeIndexStats();
      const ns = Object.entries(stats.namespaces ?? {})
        .map(([k, v]) => `- ${k || "(default)"}: ${v.recordCount} records`)
        .join("\n");
      return text(
        `Index ${name}: ${stats.totalRecordCount} records, dimension ${stats.dimension ?? "n/a"}.\nNamespaces:\n${ns || "(none)"}`,
      );
    }),
  );

  server.registerTool(
    "search",
    {
      title: "Semantic search",
      description:
        "Search the Pinecone knowledge base with a natural-language query. Use this whenever the user asks about information that may be stored in their database.",
      inputSchema: {
        query: z.string().describe("What to search for, in natural language."),
        top_k: z.number().int().min(1).max(20).default(5),
        index: indexArg,
        namespace: nsArg,
        filter: z.record(z.any()).optional().describe("Optional Pinecone metadata filter, e.g. {\"genre\": {\"$eq\": \"news\"}}"),
      },
      annotations: { readOnlyHint: true },
    },
    safe(async ({ query, top_k, index, namespace, filter }: {
      query: string; top_k: number; index?: string; namespace?: string; filter?: Record<string, unknown>;
    }) => {
      const idx = pc.index({ name: resolveIndex(index), namespace: namespace ?? DEFAULT_NAMESPACE });
      let hits: { id: string; score: number; fields: unknown }[];

      if (EMBED_MODEL) {
        // Index stores raw vectors: embed the query ourselves with Pinecone Inference.
        const emb = await pc.inference.embed({
          model: EMBED_MODEL,
          inputs: [query],
          parameters: { inputType: "query", truncate: "END" },
        });
        const values = (emb.data[0] as { values?: number[] }).values;
        if (!values) throw new Error(`Model ${EMBED_MODEL} did not return dense values`);
        const res = await idx.query({ vector: values, topK: top_k, filter, includeMetadata: true });
        hits = res.matches.map((m) => ({ id: m.id, score: m.score ?? 0, fields: m.metadata ?? {} }));
      } else {
        // Index has integrated embedding: Pinecone embeds the text for us.
        const res = await idx.searchRecords({
          query: { topK: top_k, inputs: { text: query }, filter },
        });
        hits = res.result.hits.map((h) => ({ id: h._id, score: h._score, fields: h.fields }));
      }

      if (!hits.length) return text(`No results for "${query}".`);
      const out = hits.map((h, i) => {
        const f = h.fields as Record<string, unknown>;
        const body = f[TEXT_FIELD] ?? f.chunk_text ?? f.content ?? f;
        const rest = Object.fromEntries(
          Object.entries(f).filter(([k]) => ![TEXT_FIELD, "chunk_text", "content"].includes(k)),
        );
        const meta = Object.keys(rest).length && body !== f ? `\n   metadata: ${truncate(rest)}` : "";
        return `${i + 1}. [${h.id}] (score ${h.score.toFixed(3)})\n   ${truncate(body)}${meta}`;
      });
      return text(out.join("\n\n"));
    }),
  );

  server.registerTool(
    "fetch",
    {
      title: "Fetch records",
      description: "Fetch full records (metadata) by ID from an index.",
      inputSchema: {
        ids: z.array(z.string()).min(1).max(20),
        index: indexArg,
        namespace: nsArg,
      },
      annotations: { readOnlyHint: true },
    },
    safe(async ({ ids, index, namespace }: { ids: string[]; index?: string; namespace?: string }) => {
      const idx = pc.index({ name: resolveIndex(index), namespace: namespace ?? DEFAULT_NAMESPACE });
      const res = await idx.fetch({ ids });
      const recs = Object.values(res.records ?? {});
      if (!recs.length) return text("No records found for those IDs.");
      return text(recs.map((r) => `[${r.id}]\n${JSON.stringify(r.metadata ?? {}, null, 2)}`).join("\n\n"));
    }),
  );

  if (writesEnabled) {
    server.registerTool(
      "upsert_text",
      {
        title: "Save text",
        description:
          "Save a piece of text to the Pinecone knowledge base so it can be found later. Only use when the user asks to save or remember something.",
        inputSchema: {
          text: z.string().min(1),
          id: z.string().optional().describe("Record ID. Omit to generate one."),
          metadata: z.record(z.union([z.string(), z.number(), z.boolean()])).optional(),
          index: indexArg,
          namespace: nsArg,
        },
      },
      safe(async (a: {
        text: string; id?: string; metadata?: Record<string, string | number | boolean>; index?: string; namespace?: string;
      }) => {
        const idx = pc.index({ name: resolveIndex(a.index), namespace: a.namespace ?? DEFAULT_NAMESPACE });
        const id = a.id ?? randomUUID();
        if (EMBED_MODEL) {
          const emb = await pc.inference.embed({
            model: EMBED_MODEL,
            inputs: [a.text],
            parameters: { inputType: "passage", truncate: "END" },
          });
          const values = (emb.data[0] as { values?: number[] }).values!;
          await idx.upsert({ records: [{ id, values, metadata: { ...a.metadata, [TEXT_FIELD]: a.text } }] });
        } else {
          await idx.upsertRecords({ records: [{ _id: id, ...a.metadata, [TEXT_FIELD]: a.text }] });
        }
        return text(`Saved record ${id}.`);
      }),
    );

    server.registerTool(
      "delete_records",
      {
        title: "Delete records",
        description: "Permanently delete records by ID. Confirm with the user before calling.",
        inputSchema: { ids: z.array(z.string()).min(1).max(100), index: indexArg, namespace: nsArg },
        annotations: { destructiveHint: true },
      },
      safe(async ({ ids, index, namespace }: { ids: string[]; index?: string; namespace?: string }) => {
        const idx = pc.index({ name: resolveIndex(index), namespace: namespace ?? DEFAULT_NAMESPACE });
        await idx.deleteMany({ ids });
        return text(`Deleted ${ids.length} record(s).`);
      }),
    );
  }

  return server;
}

const app = express();
app.use(express.json({ limit: "4mb" }));

app.get("/", (_req, res) => {
  res.send("Pinecone MCP server is running.");
});

const secretBuf = Buffer.from(MCP_SECRET);
const validSecret = (s: string) => {
  const b = Buffer.from(s);
  return b.length === secretBuf.length && timingSafeEqual(b, secretBuf);
};

// Stateless Streamable HTTP endpoint: https://your-host/mcp/<MCP_SECRET>
app.post("/mcp/:secret", async (req, res) => {
  if (!validSecret(req.params.secret)) {
    res.status(404).end();
    return;
  }
  const server = buildServer();
  const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
  res.on("close", () => {
    transport.close();
    server.close();
  });
  await server.connect(transport);
  await transport.handleRequest(req, res, req.body);
});

// No server-initiated streams or sessions in stateless mode.
app.all("/mcp/:secret", (_req, res) => {
  res.status(405).set("Allow", "POST").end();
});

app.listen(Number(PORT), () => {
  console.log(`Pinecone MCP listening on :${PORT} (writes ${writesEnabled ? "enabled" : "disabled"})`);
});
