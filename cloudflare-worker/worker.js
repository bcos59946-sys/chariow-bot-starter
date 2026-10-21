/**
 * Cloudflare Worker Pulse receiver.
 * Required binding: PULSE_STORE Durable Object namespace (class PulseStore)
 * Required secret: CHARIOW_PULSE_SECRET = whsec_... (Cloudflare secret, never commit it)
 */
function constantTimeEqual(a, b) {
  if (a.length !== b.length) return false;
  let result = 0;
  for (let i = 0; i < a.length; i++) result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return result === 0;
}
function hex(buffer) {
  return [...new Uint8Array(buffer)].map(b => b.toString(16).padStart(2, "0")).join("");
}
async function verify(raw, secret, signature) {
  if (!secret || !signature || !signature.startsWith("sha256=")) return false;
  const supplied = signature.slice(7).toLowerCase();
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), {name:"HMAC", hash:"SHA-256"}, false, ["sign"]);
  const digest = await crypto.subtle.sign("HMAC", key, raw);
  return constantTimeEqual(hex(digest), supplied);
}
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/" && request.method === "GET") {
      return Response.json({online:true, service:"Chariow Pulse receiver", verification:"HMAC required", fulfillment_enabled:false});
    }
    if (url.pathname !== "/chariow" || request.method !== "POST") {
      return new Response("Not found", {status:404});
    }
    const raw = await request.arrayBuffer();
    if (raw.byteLength > 32768) return new Response("Payload too large", {status:413});
    const sig = request.headers.get("x-chariow-signature");
    if (!(await verify(raw, env.CHARIOW_PULSE_SECRET, sig))) {
      return new Response("Invalid signature", {status:401});
    }
    let payload;
    try { payload = JSON.parse(new TextDecoder().decode(raw)); }
    catch { return new Response("Invalid JSON", {status:400}); }
    const deliveryId = request.headers.get("x-pulse-delivery-id");
    if (!deliveryId) return new Response("Missing delivery ID", {status:400});
    // Persist event before acknowledging. Durable Object handles idempotency by delivery ID.
    const id = env.PULSE_STORE.idFromName("chariow-pulse-events");
    const stub = env.PULSE_STORE.get(id);
    const saved = await stub.fetch("https://pulse-store/save", {
      method:"POST",
      headers:{"content-type":"application/json"},
      body:JSON.stringify({
        deliveryId,
        event: request.headers.get("x-pulse-event") || payload.event || "",
        saleId: payload.sale?.id || payload.sale_id || null,
        status: payload.sale?.status || null,
        payload
      })
    });
    if (!saved.ok) return new Response("Persistence failed", {status:503});
    return Response.json({received:true, verified:true, persisted:true, fulfillment_enabled:false}, {status:200});
  }
};
export class PulseStore {
  constructor(state) { this.state = state; }
  async fetch(request) {
    if (request.method !== "POST") return new Response("Method not allowed", {status:405});
    const item = await request.json();
    if (!item.deliveryId) return new Response("Missing delivery ID", {status:400});
    const key = `delivery:${item.deliveryId}`;
    const existing = await this.state.storage.get(key);
    if (existing) return Response.json({duplicate:true});
    await this.state.storage.put(key, item);
    // sale-level dedupe marker prevents the same sale from being treated as a new reward later.
    if (item.saleId) {
      const saleKey = `sale:${item.saleId}`;
      if (!(await this.state.storage.get(saleKey))) {
        await this.state.storage.put(saleKey, {firstDeliveryId:item.deliveryId, event:item.event, status:item.status});
      }
    }
    return Response.json({saved:true});
  }
}
