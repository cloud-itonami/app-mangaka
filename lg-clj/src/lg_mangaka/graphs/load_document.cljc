(ns lg-mangaka.graphs.load-document
  "mangaka `load_document` graph — SELECT for com.etzhayyim.mangaka.document.
  NSID: com.etzhayyim.mangaka.loadDocument
  Faithful clj port of `lg/lg_mangaka/graphs/load_document.py` (ADR-2606280030).

  Topology: START → load → END. Reads one vertex_mangaka row by vertex-id and
  returns the Genko canvas JSON stored in `props`.

  DEVIATION (noted): Python gates on RW_URL being set; here the store seam's
  `enabled?` is the analogue. No RetryPolicy in langgraph-clj (Python: 2)."
  (:require [clojure.string :as str]
            [langgraph.graph :as g]
            [lg-mangaka.store :as store]
            [lg-mangaka.audit :as audit]))
(def nsid "com.etzhayyim.mangaka.document")

(defn node-load [state]
  (let [doc-id (str/trim (str (or (:doc_id state) (:docId state) "")))]
    (cond
      (str/blank? doc-id) {:error "docId required"}
      (not (store/enabled?)) {:error "RW_URL not configured"}
      :else
      (let [app-did (:app-did (audit/config state))
            vertex-id (str "at://" app-did "/" nsid "/" doc-id)]
        (try
          (let [rows (store/select-where "vertex_mangaka" "vertex_id" vertex-id {:limit 1})
                row  (first rows)]
            (if (nil? row)
              {:error (str "document not found: " doc-id)}
              {:doc_id doc-id :docId doc-id
               :name (or (get row "name") doc-id)
               :document (or (get row "props") "")
               :convo_id "" :convoId ""
               :vertex_id (get row "vertex_id") :vertexId (get row "vertex_id")
               :error nil}))
          (catch Exception e
            (let [m (str (.. e getClass getSimpleName) ": " (.getMessage e))]
              {:error (subs m 0 (min 300 (count m)))})))))))

(defn build []
  (-> (g/state-graph)
      (g/add-node :load node-load)
      (g/set-entry-point :load)
      (g/set-finish-point :load)
      (g/compile-graph)))

(def GRAPH (build))
