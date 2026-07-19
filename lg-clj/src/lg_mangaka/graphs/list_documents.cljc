(ns lg-mangaka.graphs.list-documents
  "mangaka `list_documents` graph — catalog of com.etzhayyim.mangaka.document rows.
  NSID: com.etzhayyim.mangaka.listDocuments
  Faithful clj port of `lg/lg_mangaka/graphs/list_documents.py` (ADR-2606280030).

  Topology: START → list → END. Returns offset/limit/total per the 60-apps
  pagination convention; optionally filters by convoId (props LIKE). limit is
  clamped to [1,200], offset to >= 0.

  DEVIATION (noted): Python queries vertex_mangaka via kotoba_datomic; here the
  read goes through the `lg-mangaka.store` seam (kotoba Datom-log target)."
  (:require [clojure.string :as str]
            [langgraph.graph :as g]
            [lg-mangaka.store :as store]))

(def nsid "com.etzhayyim.mangaka.document")

(defn node-list [state]
  (let [limit   (max 1 (min 200 (int (or (:limit state) 50))))
        offset  (max 0 (int (or (:offset state) 0)))
        convo   (str/trim (str (or (:convo_id state) (:convoId state) "")))]
    (try
      (let [all   (store/select-where "vertex_mangaka" "kind" "document" {:limit 100000})
            ;; same collection scope as the Python WHERE clause
            all   (filter #(= nsid (get % "collection")) all)
            res   (if (seq convo)
                    (filter #(str/includes? (str (get % "props"))
                                            (str "\"convoId\":\"" convo "\"")) all)
                    all)
            res   (->> res
                       (sort-by #(or (get % "rkey") ""))
                       (sort-by #(or (get % "created_at") "") #(compare %2 %1)))
            total (count res)
            page  (->> res (drop offset) (take limit))
            items (mapv (fn [r]
                          {:docId (get r "rkey")
                           :name (or (get r "name") (get r "rkey"))
                           :vertexId (get r "vertex_id")
                           :createdAt (or (get r "created_at") "")})
                        page)]
        {:items items :total total :offset offset :limit limit :error nil})
      (catch Exception e
        (let [m (str (.. e getClass getSimpleName) ": " (.getMessage e))]
          {:items [] :total 0 :error (subs m 0 (min 300 (count m)))})))))

(defn build []
  (-> (g/state-graph)
      (g/add-node :list node-list)
      (g/set-entry-point :list)
      (g/set-finish-point :list)
      (g/compile-graph)))

(def GRAPH (build))
