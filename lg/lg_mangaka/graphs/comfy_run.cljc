;; ported from 60-apps/etzhayyim-project-mangaka/lg/lg_mangaka/graphs/comfy_run.py
;; real 1:1 port replacing the unit_refactor stage-0 "TODO: port-failed" stub.
;; NS fixed (root.* prefix removed) and the file is now .cljc.
;; Self-contained: no sibling stub requires; small JSON/base64 helpers inlined.
;;
;; mangaka `comfy_run` — passthrough to ComfyUI for arbitrary workflows.
;;
;; Pregel super-steps:  start → submit → poll → END
;;   submit   POST workflow → /prompt → {prompt_id}
;;   poll     loop GET /history/{prompt_id} until outputs land,
;;            then GET /view for every image node and inline its base64
;;
;; House style: state stays string-keyed (the shapes Python dicts produced);
;; Python ':kw' strings are kept AS strings; pure fns; network/host I/O is
;; behind #?(:clj ...). The Python "GRAPH = _build().compile(...)" module-level
;; LangGraph compile is omitted (it requires the langgraph runtime); `build`
;; returns the equivalent node/edge spec data so the graph topology is faithful.
(ns lg.lg-mangaka.graphs.comfy-run
  (:require [clojure.string :as str]))

;; ── default URL (authority-free portable default) ────────────────────────────
;; _DEFAULT_URL = (COMFY_POD_URL or COMFYUI_POD_URL or COMFYUI_URL
;;                 or "http://192.168.1.70:8188").rstrip("/")
(def default-comfy-url "http://192.168.1.70:8188")

(defn default-url [] default-comfy-url)

;; ── pure helpers ──────────────────────────────────────────────────────────────
(defn base-url
  "_base_url(state) — (state.get('comfy_url') or _DEFAULT_URL).rstrip('/')."
  [state]
  (let [u (let [v (or (get state "comfy_url")
                       (get-in state ["host_config" "comfy_url"]))]
            (if (and v (not= v "")) v (default-url)))]
    (str/replace u #"/+$" "")))

(defn- now-ms []
  #?(:clj (System/currentTimeMillis) :default 0))

(defn- monotonic-s []
  #?(:clj (/ (System/nanoTime) 1.0e9) :default 0.0))

(defn- b64-encode-ascii
  "base64.b64encode(bytes).decode('ascii')."
  [^bytes bs]
  #?(:clj (.encodeToString (java.util.Base64/getEncoder) bs)
     :default (throw (ex-info "bind a base64 encoder on this host" {}))))

(defn- clamp [lo hi v] (max lo (min hi v)))

;; ── submit ────────────────────────────────────────────────────────────────────
;; async def _submit(state) — POST {prompt: wf} to {base}/prompt, return prompt_id.
;; Network I/O is host-only; pure validation runs everywhere.
(defn submit
  "_submit(state). Validates the workflow then POSTs it to ComfyUI /prompt.
  Returns a state-fragment map (string-keyed), faithful to the Python."
  [state http-post json-parse]
  (let [wf (get state "workflow")]
    (if (or (not wf) (not (map? wf)))
      {"status" "error" "error" "workflow (object) required"}
      (let [base (base-url state)
            body (cond-> {"prompt" wf}
                   (get state "client_id") (assoc "client_id" (get state "client_id")))
            started-ms (now-ms)]
        (try
          (let [r (http-post (str base "/prompt") body
                             {"user-agent" "studio-comfy-run/0.1"})
                status-code (get r "status_code")]
            (if (not= status-code 200)
              {"status" "error"
               "error" (str "/prompt HTTP " status-code ": "
                            (subs (str (get r "text" "")) 0 (min 300 (count (str (get r "text" ""))))))
               "started_at_ms" started-ms}
              (let [j (or (json-parse (get r "text")) {})
                    pid (get j "prompt_id")]
                (if (not pid)
                  {"status" "error"
                   "error" (str "/prompt missing prompt_id: " j)
                   "started_at_ms" started-ms}
                  {"prompt_id" pid
                   "number" (long (or (get j "number") 0))
                   "submit_response" j
                   "started_at_ms" started-ms}))))
          (catch #?(:clj Exception :default :default) exc
            {"status" "error"
             "error" (str "POST /prompt failed: " #?(:clj (.getMessage ^Exception exc) :default exc))
             "started_at_ms" started-ms}))))))

;; ── poll ────────────────────────────────────────────────────────────────────
;; async def _poll(state) — loop GET /history/{pid}, then GET /view per image node.
;; http-get : (url headers params) -> {"status_code" int "json" data "content" bytes "headers" {}}
;; sleep-ms : (ms) -> nil
(defn poll
  "_poll(state). Polls ComfyUI /history until outputs land, fetches each image
  via /view and inlines base64. Faithful to the Python control flow."
  [state http-get sleep-ms]
  (if (= (get state "status") "error")
    {"status" "error"
     "elapsed_ms" (- (now-ms) (long (or (get state "started_at_ms") 0)))}
    (let [base (base-url state)
          pid (or (get state "prompt_id") "")]
      (if (= pid "")
        {"status" "error" "error" "no prompt_id"}
        (let [timeout-s (clamp 10 900 (long (or (get state "timeout_seconds") 300)))
              interval-ms (clamp 250 10000 (long (or (get state "poll_interval_ms") 1500)))
              deadline (+ (monotonic-s) timeout-s)
              headers {"user-agent" "studio-comfy-run/0.1"}]
          (loop [last-history {}]
            (if (< (monotonic-s) deadline)
              (let [hr (try (http-get (str base "/history/" pid) headers nil)
                            (catch #?(:clj Exception :default :default) _exc ::http-fail))]
                (cond
                  (= hr ::http-fail) (do (sleep-ms (/ interval-ms 1000.0)) (recur last-history))
                  (not= (get hr "status_code") 200) (do (sleep-ms (/ interval-ms 1000.0)) (recur last-history))
                  :else
                  (let [entry (get (or (get hr "json") {}) pid)]
                    (if (not entry)
                      (do (sleep-ms (/ interval-ms 1000.0)) (recur entry))
                      (let [status (or (get entry "status") {})
                            messages (or (get status "messages") [])
                            err-msg (some (fn [m]
                                            (when (and (sequential? m) (>= (count m) 2)
                                                       (contains? #{"execution_error" "execution_interrupted"} (first m)))
                                              m))
                                          messages)]
                        (if err-msg
                          {"status" "error"
                           "error" (str (first err-msg) ": "
                                        (let [s (str (second err-msg))] (subs s 0 (min 400 (count s)))))
                           "raw_history" entry
                           "elapsed_ms" (- (now-ms) (long (or (get state "started_at_ms") 0)))}
                          (let [outputs (or (get entry "outputs") {})]
                            (if (empty? outputs)
                              (do (sleep-ms (/ interval-ms 1000.0)) (recur entry))
                              ;; fetch every image artifact from every node that produced one
                              (let [result
                                    (reduce
                                     (fn [images [node-id node-out]]
                                       (reduce
                                        (fn [images img]
                                          (let [params {"filename" (get img "filename" "")
                                                        "subfolder" (get img "subfolder" "")
                                                        "type" (get img "type" "output")}
                                                vr (try (http-get (str base "/view") headers params)
                                                        (catch #?(:clj Exception :default :default) exc
                                                          (reduced
                                                           {::error
                                                            {"status" "error"
                                                             "error" (str "/view fetch failed: "
                                                                          #?(:clj (.getMessage ^Exception exc) :default exc))
                                                             "raw_history" entry
                                                             "elapsed_ms" (- (now-ms) (long (or (get state "started_at_ms") 0)))}})))]
                                            (cond
                                              (and (map? vr) (contains? vr ::error)) (reduced vr)
                                              (not= (get vr "status_code") 200) images
                                              :else
                                              (let [body (or (get vr "content") (byte-array 0))]
                                                (conj images
                                                      {"node" (str node-id)
                                                       "filename" (get params "filename")
                                                       "type" (get params "type")
                                                       "subfolder" (get params "subfolder")
                                                       "imageInlineB64" (b64-encode-ascii body)
                                                       "imageMime" (get (or (get vr "headers") {}) "content-type" "image/png")
                                                       "byteLen" (count body)})))))
                                        images
                                        (or (get node-out "images") [])))
                                     []
                                     outputs)]
                                (if (and (map? result) (contains? result ::error))
                                  (get result ::error)
                                  {"status" "ok"
                                   "images" result
                                   "raw_history" entry
                                   "elapsed_ms" (- (now-ms) (long (or (get state "started_at_ms") 0)))}))))))))))
              ;; deadline exceeded
              {"status" "timeout"
               "error" (str "poll deadline " timeout-s "s exceeded")
               "raw_history" last-history
               "elapsed_ms" (- (now-ms) (long (or (get state "started_at_ms") 0)))})))))))

;; ── build ────────────────────────────────────────────────────────────────────
;; def _build() -> StateGraph: submit (retry max_attempts=2) → poll.
;; The langgraph compile is omitted; we return the topology as data (faithful nodes/edges).
(defn build
  "_build() — returns the comfy_run graph topology as a data spec
  (nodes + retry policy + edges). The langgraph runtime compile is omitted."
  []
  {"nodes" [{"name" "submit" "fn" "submit" "retry_policy" {"max_attempts" 2}}
            {"name" "poll" "fn" "poll"}]
   "edges" [["START" "submit"] ["submit" "poll"] ["poll" "END"]]})

;; GRAPH = _build().compile(name="comfy_run")  — omitted (langgraph runtime).
(def graph (build))
