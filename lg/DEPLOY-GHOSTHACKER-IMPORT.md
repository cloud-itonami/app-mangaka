# lg-mangaka — Ghosthacker Import Deploy Runbook

Phase A (LOCAL implementation) was completed 2026-05-12 by the import task. This file lists the remaining production-touching steps. Each step is gated; execute one at a time and verify before continuing.

## Phase A — Code (DONE)

| Item | Status |
|---|---|
| `lg_mangaka/graphs/save_document.py` — RW INSERT into `vertex_mangaka` (delete-then-insert, RW-compat) | ✅ |
| `lg_mangaka/graphs/load_document.py` — SELECT by `vertex_id` | ✅ |
| `lg_mangaka/graphs/list_documents.py` — paginated list | ✅ |
| `lg_mangaka/server.py` — graphs registered in `GRAPHS` + `_NSID_TO_ASSISTANT` | ✅ |
| `langgraph.json` — graphs registered | ✅ |
| Python syntax validated | ✅ |
| Lexicon `string` ID migration (saveDocument/loadDocument/createProject/saveProject/listProjects) + bundle regen + mangaka Worker redeploy | ✅ (separate step, 2026-05-12) |

## Phase B — Image build (TODO, ~5-10 min)

Build a new `lg-mangaka` image with the three new graphs and push to GHCR.

```bash
# Pre-flight
gh auth status                      # must be signed in (for ghcr.io)
docker buildx ls | grep etzhayyim-vke    # confirm BuildKit remote builder is up

# Build + push (linux/amd64 for VKE)
cd 60-apps/etzhayyim-project-mangaka/lg
TAG=0.1.1-$(date +%Y%m%d%H%M%S)-amd64
docker buildx build \
  --builder etzhayyim-vke \
  --platform linux/amd64 \
  --build-context py=../../../40-engine/kotoba/crates/kotoba-kotodama/py \
  --cache-from type=registry,ref=ghcr.io/etzhayyim/build-cache:main \
  --cache-to   type=registry,ref=ghcr.io/etzhayyim/build-cache:main,mode=max \
  -t ghcr.io/etzhayyim/lg-mangaka:${TAG} \
  --push .

# Capture sha256
IMAGE_SHA=$(docker buildx imagetools inspect ghcr.io/etzhayyim/lg-mangaka:${TAG} | awk '/Digest:/{print $2}')
echo "fullRef: ghcr.io/etzhayyim/lg-mangaka:${TAG}@${IMAGE_SHA}"
```

Update `50-infra/vultr/lg-mangaka-pool/values.yaml`:
```yaml
image:
  tag: "<TAG>"
  fullRef: "ghcr.io/etzhayyim/lg-mangaka:<TAG>@sha256:<SHA>"
```

## Phase C — Helm deploy to Vultr VKE (TODO, ~2-3 min)

```bash
# Confirm kubecontext is Vultr VKE (mitama-udf cluster)
kubectl config current-context           # must point at Vultr VKE
kubectl get ns mitama-udf                # must exist (already used by lg-shinshi, dispatcher)

# Install / upgrade
cd 50-infra/vultr/lg-mangaka-pool
helm upgrade --install lg-mangaka-pool . \
  --namespace mitama-udf \
  --wait \
  --timeout 5m

# Verify pod healthy
kubectl -n mitama-udf get pods -l app.kubernetes.io/name=lg-mangaka
kubectl -n mitama-udf logs -l app.kubernetes.io/name=lg-mangaka --tail=50

# In-cluster smoke
kubectl -n mitama-udf run smoke --rm -i --image=curlimages/curl --restart=Never -- \
  curl -sS -X POST -H 'content-type: application/json' \
  -d '{}' http://lg-mangaka.mitama-udf.svc.cluster.local:8000/xrpc/com.etzhayyim.mangaka.health
# expect: {"ok": true, ...}
```

## Phase D — Cloudflared tunnel (TODO, ~5 min)

`lg-mangaka.etzhayyim.com` needs to route to the in-cluster service. Two routes work:

### Option 1 — Add to existing `bpmn-dispatcher-tunnel` (recommended)

Add a hostname mapping to `50-infra/vultr/cloudflared/bpmn-dispatcher-tunnel.yaml`:
```yaml
ingress:
  - hostname: dispatcher.etzhayyim.com
    service: http://bpmn-dispatcher.mitama-udf.svc.cluster.local:8080
  - hostname: lg-mangaka.etzhayyim.com
    service: http://lg-mangaka.mitama-udf.svc.cluster.local:8000
  - service: http_status:404
```

```bash
kubectl apply -f 50-infra/vultr/cloudflared/bpmn-dispatcher-tunnel.yaml
# In Cloudflare dashboard / Terraform: CNAME lg-mangaka.etzhayyim.com → <tunnel-uuid>.cfargotunnel.com (proxied)
```

### Option 2 — Route via atproto.etzhayyim.com dispatcher

Update `50-infra/cloudflare/workers/atproto/src/yoro-reactive-dispatch.ts` to route `com.etzhayyim.mangaka.saveDocument` etc. to `http://lg-mangaka.mitama-udf.svc.cluster.local:8000` via the same CF Tunnel hop. Then `mangaka.etzhayyim.com` continues to proxy to `dispatcher.etzhayyim.com` (no tunnel update needed) and the dispatcher forwards based on NSID prefix.

## Phase E — Run the ghosthacker import (TODO, ~10-30 min depending on size)

Once `https://lg-mangaka.etzhayyim.com/xrpc/com.etzhayyim.mangaka.saveDocument` returns 200 in a smoke test:

```bash
# Update import-jump-all.ts to target lg-mangaka instead of mangaka.etzhayyim.com
# (one-line change: const MANGAKA_BASE = "https://lg-mangaka.etzhayyim.com/xrpc/";)
# Or pass via env: LG_MANGAKA_BASE=https://lg-mangaka.etzhayyim.com/xrpc/ deno run ...

# Then:
cd 60-apps/etzhayyim-project-mangaka
deno run --allow-read --allow-net --allow-run --allow-write --allow-env \
  scripts/import-jump-all.ts
```

Verification:
```bash
# 1 row per episode in vertex_mangaka with kind='document'
PGPASSWORD=... psql "$KAISYA_URL" -c \
  "SELECT rkey, name FROM vertex_mangaka WHERE kind='document' AND collection='com.etzhayyim.mangaka.document' ORDER BY rkey;"

# Web UI deep-link (after frontend loadDocument wired to lg-mangaka path)
open "https://mangaka.etzhayyim.com/at/mng4k4x1.etzhayyim.com/com.etzhayyim.mangaka.document/doc-gh-arc0-1-origin"
```

## Phase F — Frontend `loadDocument` wiring (TODO, ~30 min)

The mangaka Svelte SPA calls `loadDocument({ docId })` somewhere. Update its base URL to either:
- Call `https://mangaka.etzhayyim.com/xrpc/com.etzhayyim.mangaka.loadDocument` (proxied to lg-mangaka via dispatcher / tunnel)
- Or call `https://lg-mangaka.etzhayyim.com/xrpc/com.etzhayyim.mangaka.loadDocument` directly

Confirm the response shape matches what Genko canvas expects (`{ document: "<JSON string>" }`).

## Rollback

```bash
# Phase C rollback
helm rollback lg-mangaka-pool -n mitama-udf

# Phase D rollback (remove ingress entry, re-apply tunnel)

# Phase E rollback (delete imported rows)
psql "$ROOT_URL" -c \
  "DELETE FROM vertex_mangaka WHERE kind='document' AND collection='com.etzhayyim.mangaka.document' AND rkey LIKE 'doc-gh-%';"
```

## Open risks / decisions

- The XRPC adapter in `server.py` strips JSON-LD nesting via `_camel_to_snake`. If the Genko `document` field arrives nested, ensure the lexicon validator (already updated for string `docId`/`convoId`) doesn't reject. Smoke with a 1-page test doc first.
- BPMN audit shim (`emit_audit_bg`) requires `BPMN_DISPATCHER_INTERNAL_SECRET` — already wired in helm values; `LG_AUDIT_DISABLED=true` can be set to suppress during initial validation.
- `vertex_mangaka` has 26 columns; we populate 18. The rest (parent_rkey, page_number, panel_number, asset_type, mime_type, cid, props, description, _seq) are nullable.
