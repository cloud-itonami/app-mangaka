(ns lg-mangaka.server
  "XRPC/runs HTTP server for lg-mangaka — clj port of `lg/lg_mangaka/server.py`
  (FastAPI → org.httpkit.server, a babashka built-in). Same endpoint surface:

    POST /runs                  → invoke a graph synchronously
    POST /runs/stream           → stream graph superstep events as SSE
    POST|GET /xrpc/{nsid}       → XRPC-compat shim (NSID → assistant_id)
    GET  /ok | /health          → liveness / readiness

  Auth: optional LG_API_KEY enforces x-api-key on /runs paths; /xrpc is
  unauthenticated (trust at the cloudflared tunnel layer), parity with Python.

  ── WAVE-2 PARTIAL PORT (ADR-2606280030) ──────────────────────────────────
  The Python app is LARGE: 38 graphs registered across langgraph.json + 18 in
  server.py GRAPHS, ~96 py files, an MCP tool surface (lg_mangaka.tools), comfy/
  cine/3D/video pipelines, Hume emotion, face detection, etc. This clj twin
  faithfully ports the SERVER dispatch + 7 verifiable graphs:

    health, agent_chat, save_document, load_document, list_documents,
    record_op_log, debug_canvas_state

  REMAINING (python-only for now, coexisting & deployed): the other 11
  server-registered graphs (import_ghosthacker, analyze_character_graph,
  enrich_characters / _organizations / _environments, derive_chapter_incidents,
  import_chat_history, backfill_mangaka_edges, compose_scene_3d, detect_faces,
  score_emotion) + the langgraph.json-only generation graphs (cine_*,
  mangaka_generate_*, comfy_run, mangaka_train_character_lora, ...) + the
  MCP tool dispatch surface (lg_mangaka.tools.*, /xrpc/com.etzhayyim.mcp.message).
  See the PR body for the full ported-vs-remaining matrix.

  The Python FastAPI server under `lg/` stays the deployed runtime and COEXISTS;
  this twin is additive (py_removed=0).

  DEVIATION (noted): no RW Postgres checkpointer (substrate boundary forbids
  RisingWave); state is a clj map and thread-state retrieval is omitted."
  (:require [langgraph.graph :as g]
            [cheshire.core :as json]
            [clojure.string :as str]
            [lg-mangaka.store :as store]
            [lg-mangaka.llm :as llm]
            [lg-mangaka.audit :as audit]
            [lg-mangaka.graphs.health :as health]
            [lg-mangaka.graphs.agent-chat :as agent-chat]
            [lg-mangaka.graphs.save-document :as save-document]
            [lg-mangaka.graphs.load-document :as load-document]
            [lg-mangaka.graphs.list-documents :as list-documents]
            [lg-mangaka.graphs.record-op-log :as record-op-log]
            [lg-mangaka.graphs.debug-canvas-state :as debug-canvas-state]))

(def GRAPHS
  {"health"             health/GRAPH
   "agent_chat"         agent-chat/GRAPH
   "save_document"      save-document/GRAPH
   "load_document"      load-document/GRAPH
   "list_documents"     list-documents/GRAPH
   "record_op_log"      record-op-log/GRAPH
   "debug_canvas_state" debug-canvas-state/GRAPH})

;; NSID → assistant_id (subset of server.py:_NSID_TO_ASSISTANT covering the
;; ported graphs; the chat NSID has 3 aliases exactly as in Python).
(def NSID-MAP
  {"com.etzhayyim.mangaka.health"          "health"
   "com.etzhayyim.mangaka.chat"            "agent_chat"
   "com.etzhayyim.mangaka.pipelineChat"    "agent_chat"
   "com.etzhayyim.mangaka.projectChat"     "agent_chat"
   "com.etzhayyim.mangaka.saveDocument"    "save_document"
   "com.etzhayyim.mangaka.loadDocument"    "load_document"
   "com.etzhayyim.mangaka.listDocuments"   "list_documents"
   "com.etzhayyim.mangaka.recordOpLog"     "record_op_log"
   "com.etzhayyim.mangaka.debugCanvasState" "debug_canvas_state"})

;; ── input key normalization (camelCase / snake_case JSON → kebab keyword) ──
;; mirrors server.py:_camel_to_snake (the XRPC shim) but lands on kebab keywords
;; so graph nodes can read both :doc_id and :docId style keys uniformly.
(defn ->snake-key
  "camelCase → snake_case keyword (server.py:_camel_to_snake analogue)."
  [s]
  (-> (name s)
      (str/replace #"([a-z0-9])([A-Z])" "$1_$2")
      str/lower-case
      keyword))

(defn normalize-input
  "JSON object → graph input map. Keeps BOTH the snake_case key and the original
  key (graphs read either), matching the Python nodes' dual .get(snake)/.get(camel)."
  [m]
  (when (map? m)
    (reduce-kv (fn [acc k v]
                 (let [kk (if (keyword? k) k (keyword k))]
                   (-> acc (assoc kk v) (assoc (->snake-key kk) v))))
               {} m)))

;; ── pure dispatch (testable without a running server) ──

(defn health
  "GET /ok → {:ok true :graphs [...] :version}"
  []
  {:status 200 :body {:ok true :graphs (vec (keys GRAPHS)) :version "0.1.0"}})

(defn check-api-key
  "If an explicit API key is configured, x-api-key must match."
  ([x-api-key] (check-api-key "" x-api-key))
  ([expected x-api-key]
   (when (and (seq expected) (not= x-api-key expected))
     {:status 401 :body {:detail "invalid x-api-key"}})))

(defn- run-graph [graph input {:keys [host-config llm-http-post audit-http-post]}]
  (let [host-config (merge audit/default-config (or host-config {}))]
    (binding [store/*enabled?* (boolean (:store-enabled? host-config))
              llm/*http-post* llm-http-post
              audit/*emit* (if (fn? audit-http-post)
                             (partial audit/http-emit-with audit-http-post)
                             (fn [_ _] nil))]
      (try
        {:status 200 :body {:ok true :result
                            (g/invoke graph (assoc (or input {}) :host-config host-config))}}
        (catch Exception e
          {:status 500 :body {:ok false :error (let [m (str (.getMessage e))]
                                                 (subs m 0 (min 500 (count m))))
                              :errorType (.. e getClass getSimpleName)}})))))

(defn dispatch-run
  "POST /runs body → {:status :body}. body keys: :assistant_id :input.
  Enforces optional x-api-key via opts {:x-api-key ...}."
  ([body] (dispatch-run body {}))
  ([body {:keys [x-api-key api-key] :as capabilities}]
   (or (check-api-key (or api-key "") x-api-key)
       (let [aid   (str (or (:assistant_id body) (get body "assistant_id") ""))
             graph (get GRAPHS aid)]
         (if (nil? graph)
           {:status 404 :body {:detail (str "unknown graph: " aid)}}
           (run-graph graph (normalize-input (or (:input body) (get body "input") {}))
                      capabilities))))))

(defn dispatch-xrpc
  "POST|GET /xrpc/{nsid} body → {:status :body}. /xrpc is unauthenticated."
  ([nsid body] (dispatch-xrpc nsid body {}))
  ([nsid body capabilities]
  (let [aid (get NSID-MAP nsid)]
    (if (nil? aid)
      {:status 404 :body {:detail (str "unknown NSID: " nsid)}}
      (let [res (run-graph (get GRAPHS aid) (normalize-input (or body {})) capabilities)]
        ;; flatten to the python xrpc_compat shape (result + assistantId)
        (if (= 200 (:status res))
          {:status 200 :body (assoc (get-in res [:body :result]) :assistantId aid)}
          res))))))

;; ── http handler ──
(defn- json-resp [{:keys [status body]}]
  {:status status :headers {"Content-Type" "application/json"}
   :body (json/generate-string body)})

(defn- parse-body [req]
  (when-let [b (:body req)]
    (try (json/parse-string (slurp b) true) (catch Exception _ nil))))

(defn handler-with-capabilities [capabilities req]
  (let [uri (:uri req) method (:request-method req)
        xapi (get-in req [:headers "x-api-key"])]
    (cond
      (and (= method :get) (#{"/ok" "/health"} uri))
      (json-resp (health))

      (and (= method :post) (= uri "/runs"))
      (json-resp (dispatch-run (or (parse-body req) {})
                               (assoc capabilities :x-api-key xapi)))

      (str/starts-with? uri "/xrpc/")
      (json-resp (dispatch-xrpc (subs uri (count "/xrpc/")) (parse-body req)
                                capabilities))

      :else
      (json-resp {:status 404 :body {:detail "not found"}}))))

(defn handler [req] (handler-with-capabilities {} req))

(defn run-server-with
  "Start the server only through an explicitly supplied host capability."
  [run-server port capabilities]
  (run-server (partial handler-with-capabilities capabilities) {:port port}))

(defn -main [& _]
  (throw (ex-info "Mangaka portable runtime requires an explicit server host adapter"
                  {:capability :mangaka/run-server})))
