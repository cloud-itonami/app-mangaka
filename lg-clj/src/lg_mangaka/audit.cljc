(ns lg-mangaka.audit
  "Fire-and-forget BPMN `generic.audit.emit` shim — clj port of `lg/lg_mangaka/audit.py`
  (ADR-2606280030).

  httpx → babashka.http-client; JSON → cheshire. The POST is best-effort: any
  failure is logged to *err* and swallowed (non-fatal), exactly like the Python
  (audit loss is acceptable — the LangGraph state checkpoint is the source of
  truth for resumability). Honors LG_AUDIT_DISABLED=1 (the test harness sets it)."
  (:require [cheshire.core :as json]
            [clojure.string :as str]))

(def default-config
  {:app-did "did:web:mangaka.etzhayyim.com"
   :default-org-did "did:erc725:etzhayyim:260425:etzhayyim-japan"
   :store-enabled? false
   :dispatcher-url "http://bpmn-dispatcher.mitama-udf.svc.cluster.local:8080"
   :internal-secret "" :audit-timeout-ms 3000
   :llm {:url "http://127.0.0.1:4000/v1" :model "gemma3:4b" :timeout-ms 60000}})

(defn config [state] (merge default-config (or (:host-config state) {})))

(defn http-emit-with [http-post host-config payload]
  (when-not (fn? http-post)
    (throw (ex-info "Mangaka audit requires an explicit HTTP POST capability"
                    {:capability :mangaka/audit-http-post})))
  (let [{:keys [dispatcher-url internal-secret audit-timeout-ms]}
        (merge default-config host-config)
        headers (cond-> {"Content-Type" "application/json"}
                  (seq internal-secret) (assoc "x-internal-trust" internal-secret))]
    (http-post (str (str/replace dispatcher-url #"/+$" "")
                    "/xrpc/com.etzhayyim.generic.audit.emit")
               {:headers headers :body (json/generate-string payload)
                :timeout audit-timeout-ms :throw false})))

(def ^:dynamic *emit* (fn [_host-config _payload] nil))

(defn emit-audit!
  "Synchronous best-effort emit. Returns nil. Never throws.
  Keys: :actor :activity :object-id :object-type :attributes."
  [host-config {:keys [actor activity object-id object-type attributes]}]
  (try
      (*emit* host-config {:actor actor :activity activity :objectId object-id
                           :objectType object-type :attributes (or attributes {})})
      (catch Exception e
        (binding [*out* *err*]
          (println "audit.emit failed (non-fatal):" (.getMessage e)
                   "| activity=" activity "id=" object-id))))
    nil)

(defn emit-audit-bg
  "Fire-and-forget: schedules emit-audit! on a future
  (the asyncio.create_task analogue in audit.py)."
  [state m]
  (future (emit-audit! (config state) m)))
