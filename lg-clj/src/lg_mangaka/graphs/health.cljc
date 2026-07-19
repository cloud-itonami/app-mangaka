(ns lg-mangaka.graphs.health
  "mangaka `health` graph — simplest end-to-end probe.
  NSID: com.etzhayyim.mangaka.health
  Faithful clj port of `lg/lg_mangaka/graphs/health.py` (ADR-2606280030).

  Topology: START → check_rw → summarize → emit_audit → END.
    check_rw   probes the store (Python: get_kotoba_client().q(...)); the store
               seam replaces the RisingWave/kotoba_datomic client.
    summarize  sets ok = rw_ok, stamps server_now.
    emit_audit fire-and-forget OCEL event (best-effort, swallowed).

  DEVIATION (noted): langgraph-clj has no RetryPolicy; the Python `check_rw`
  carried max_attempts=2. The node body is identical; the retry wrapper is
  dropped (see server.cljc docstring)."
  (:require [langgraph.graph :as g]
            [lg-mangaka.store :as store]
            [lg-mangaka.audit :as audit]))

(defn- now-iso []
  (.format (java.time.format.DateTimeFormatter/ofPattern "yyyy-MM-dd'T'HH:mm:ss'Z'")
           (java.time.ZonedDateTime/now (java.time.ZoneOffset/UTC))))

(defn node-check-rw [_state]
  (try
    (let [started (System/nanoTime)]
      (store/q "[:find (pull ?e [*]) :where [?e :db/ident _]]")
      {:rw_ok true
       :rw_latency_ms (int (/ (- (System/nanoTime) started) 1000000))})
    (catch Exception e
      (let [m (str "rw: " (.getMessage e))]
        {:rw_ok false :error (subs m 0 (min 200 (count m)))}))))

(defn node-summarize [state]
  {:ok (boolean (:rw_ok state))
   :server_now (now-iso)})

(defn node-emit-audit [state]
  (audit/emit-audit-bg
   state
   {:actor (:app-did (audit/config state))
    :activity "mangaka.health.check"
    :object-id (str "health:" (quot (System/currentTimeMillis) 1000))
    :object-type "mangaka.health"
    :attributes {:ok (:ok state false)
                 :rwOk (:rw_ok state false)
                 :rwLatencyMs (:rw_latency_ms state 0)}})
  {})

(defn build []
  (-> (g/state-graph)
      (g/add-node :check_rw node-check-rw)
      (g/add-node :summarize node-summarize)
      (g/add-node :emit_audit node-emit-audit)
      (g/add-edge :check_rw :summarize)
      (g/add-edge :summarize :emit_audit)
      (g/set-entry-point :check_rw)
      (g/set-finish-point :emit_audit)
      (g/compile-graph)))

(def GRAPH (build))
