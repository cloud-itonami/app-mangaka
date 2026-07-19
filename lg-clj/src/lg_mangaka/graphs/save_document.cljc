(ns lg-mangaka.graphs.save-document
  "mangaka `save_document` graph — persistence for com.etzhayyim.mangaka.document.
  NSID: com.etzhayyim.mangaka.saveDocument
  Faithful clj port of `lg/lg_mangaka/graphs/save_document.py` (ADR-2606280030).

  Topology: START → save → emit_audit → END.
    save        INSERT one vertex_mangaka row (kind='document') via the store
                seam (Python: get_kotoba_client().insert_row); idempotent
                delete-then-insert behavior delegated to the store backend.
    emit_audit  fire-and-forget OCEL event.

  DEVIATION (noted): Python persists to RisingWave via kotoba_datomic; here the
  write is the injectable `lg-mangaka.store` seam (kotoba Datom-log target).
  langgraph-clj has no RetryPolicy (Python save had max_attempts=2)."
  (:require [clojure.string :as str]
            [langgraph.graph :as g]
            [lg-mangaka.store :as store]
            [lg-mangaka.audit :as audit]))

(def nsid "com.etzhayyim.mangaka.document")

(defn- s [v] (str/trim (str (or v ""))))

(defn node-save [state]
  (let [doc-id (s (or (:doc_id state) (:docId state)))]
    (if (str/blank? doc-id)
      {:status "error" :error "docId required"}
      (let [{:keys [app-did default-org-did]} (audit/config state)
            name      (let [n (s (:name state))] (if (seq n) n doc-id))
            document  (or (:document state) "")
            actor-did (let [a (s (or (:actor_did state) (:actorDid state)))]
                        (if (seq a) a app-did))
            org-did   (let [o (s (or (:org_did state) (:orgDid state)))]
                        (if (seq o) o default-org-did))
            vertex-id (str "at://" app-did "/" nsid "/" doc-id)
            now-iso   (store/now-iso)]
        (try
          (store/insert-row! "vertex_mangaka"
                             {"vertex_id" vertex-id "created_date" (subs now-iso 0 10)
                              "sensitivity_ord" 0 "owner_did" app-did "rkey" doc-id
                              "repo" app-did "did" app-did "collection" nsid
                              "label" "document" "title" name "name" name
                              "display_name" name "kind" "document" "status" "saved"
                              "created_at" now-iso "props" document
                              "actor_did" actor-did "org_did" org-did})
          {:status "saved" :doc_id doc-id :docId doc-id
           :vertex_id vertex-id :vertexId vertex-id :error nil}
          (catch Exception e
            (let [m (str (.. e getClass getSimpleName) ": " (.getMessage e))]
              {:status "error" :error (subs m 0 (min 300 (count m)))})))))))

(defn node-emit-audit [state]
  (let [app-did (:app-did (audit/config state))]
    (audit/emit-audit-bg
     state
     {:actor (or (:actor_did state) (:actorDid state) app-did)
    :activity "mangaka.document.save"
    :object-id (or (:vertex_id state) (:vertexId state)
                   (:doc_id state) (:docId state) "")
    :object-type "mangaka.document"
    :attributes {:docId (or (:doc_id state) (:docId state))
                 :ok (= "saved" (:status state))
                   :error (:error state)}}))
  {})

(defn build []
  (-> (g/state-graph)
      (g/add-node :save node-save)
      (g/add-node :emit_audit node-emit-audit)
      (g/add-edge :save :emit_audit)
      (g/set-entry-point :save)
      (g/set-finish-point :emit_audit)
      (g/compile-graph)))

(def GRAPH (build))
