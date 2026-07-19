(ns lg-mangaka.graphs.record-op-log
  "mangaka `record_op_log` graph — append a change-history entry as kind=opLog in
  vertex_mangaka, plus link it to the parent document via edge_mangaka_emits_op.
  NSID: com.etzhayyim.mangaka.recordOpLog
  Faithful clj port of `lg/lg_mangaka/graphs/record_op_log.py` (ADR-2606280030).

  Topology (Pregel 2-step): START → build_row → write_row → END.
    build_row  assign rkey + parent + props from input (pure).
    write_row  INSERT vertex_mangaka(kind=opLog) + INSERT edge_mangaka_emits_op
               via the store seam.

  DEVIATION (noted): Python writes RisingWave via kotoba_datomic; here the two
  INSERTs go through `lg-mangaka.store`. No RetryPolicy in langgraph-clj
  (Python write_row had max_attempts=2)."
  (:require [clojure.string :as str]
            [cheshire.core :as json]
            [langgraph.graph :as g]
            [lg-mangaka.store :as store]
            [lg-mangaka.audit :as audit]))

(defn short-nid [nid]
  (let [n (str/replace (str (or nid "")) #"^[nN]+" "")
        c (subs n 0 (min 10 (count n)))]
    (if (seq c) c "anon")))

(defn node-build-row [state]
  (let [doc-id (str/trim (str (or (:doc_id state) "")))]
    (if (str/blank? doc-id)
      {:status "error" :error "docId required"}
      (let [{:keys [app-did default-org-did]} (audit/config state)
            op        (str/trim (str (or (:op state) "other")))
            nid       (str/trim (str (or (:nid state) "")))
            node-type (str/trim (str (or (:node_type state) "")))
            ts-ms     (System/currentTimeMillis)
            rkey      (str "op-" doc-id "-" ts-ms "-" (short-nid nid))
            name      (str/trim (str op " " node-type " " (short-nid nid)))
            now-iso   (store/now-iso)
            actor-did (let [a (str/trim (str (or (:actor_did state) "")))]
                        (if (seq a) a app-did))
            org-did   (let [o (str/trim (str (or (:org_did state) "")))]
                        (if (seq o) o default-org-did))
            props     {:op op :nid nid :nodeType node-type
                       :before (or (:before state) "") :after (or (:after state) "")
                       :actor actor-did :docId doc-id :ts ts-ms}
            row       {:vid (str "at://" app-did "/com.etzhayyim.mangaka.opLog/" rkey)
                       :rkey rkey :parent_rkey doc-id :name name :now_iso now-iso
                       :props props :actor_did actor-did :org_did org-did
                       :doc_vid (str "at://" app-did "/com.etzhayyim.mangaka.document/" doc-id)}]
        {:row row}))))

(defn node-write-row [state]
  (let [row (:row state)
        app-did (:app-did (audit/config state))]
    (if (nil? row)
      {:status "error" :error (or (:error state) "row missing")}
      (try
        (store/insert-row! "vertex_mangaka"
                           {"vertex_id" (:vid row) "created_date" (subs (:now_iso row) 0 10)
                            "sensitivity_ord" 0 "owner_did" app-did "rkey" (:rkey row)
                            "repo" app-did "did" app-did
                            "collection" "com.etzhayyim.mangaka.opLog" "label" "opLog"
                            "title" (:name row) "name" (:name row) "display_name" (:name row)
                            "kind" "opLog" "status" "recorded" "created_at" (:now_iso row)
                            "props" (json/generate-string (:props row))
                            "parent_rkey" (:parent_rkey row)
                            "actor_did" (:actor_did row) "org_did" (:org_did row)})
        (store/insert-row! "edge_mangaka_emits_op"
                           {"edge_id" (str "emits_op:" (:parent_rkey row) ":" (:rkey row))
                            "src_vid" (:doc_vid row) "dst_vid" (:vid row) "_seq" 0
                            "created_date" (subs (:now_iso row) 0 10)
                            "sensitivity_ord" 0 "owner_did" app-did})
        {:status "recorded" :rkey (:rkey row) :error nil}
        (catch Exception e
          (let [m (str (.. e getClass getSimpleName) ": " (.getMessage e))]
            {:status "error" :error (subs m 0 (min 300 (count m)))}))))))

(defn build []
  (-> (g/state-graph)
      (g/add-node :build_row node-build-row)
      (g/add-node :write_row node-write-row)
      (g/add-edge :build_row :write_row)
      (g/set-entry-point :build_row)
      (g/set-finish-point :write_row)
      (g/compile-graph)))

(def GRAPH (build))
