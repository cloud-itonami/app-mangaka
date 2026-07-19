(ns lg-mangaka.graphs.debug-canvas-state
  "mangaka `debug_canvas_state` graph — 4-step diagnostic for the canvas UX.
  Loads the live Genko document for a docId and analyses ai-image / panel rect
  overlap (helps debug selection / resize behaviour).
  NSID: com.etzhayyim.mangaka.debugCanvasState
  Faithful clj port of `lg/lg_mangaka/graphs/debug_canvas_state.py` (ADR-2606280030).

  Topology (Pregel 4-step): START → load_doc → parse_nodes → compute_rect_diff →
  summarise → END.
    load_doc          SELECT props for the document via the store seam.
    parse_nodes       walk pages[].nodes[], normalize to {nid type parent x1..y2}.
    compute_rect_diff per ai-image find parent panel, classify rect overlap.
    summarise         aggregate per-page + per-doc, emit verdict.

  DEVIATION (noted): load_doc reads through `lg-mangaka.store` (kotoba Datom-log
  target) instead of RisingWave. No RetryPolicy in langgraph-clj (Python: 2).
  Steps 2-4 are pure and fully verifiable offline."
  (:require [clojure.string :as str]
            [cheshire.core :as json]
            [langgraph.graph :as g]
            [lg-mangaka.store :as store]
            [lg-mangaka.audit :as audit]))
(def nsid "com.etzhayyim.mangaka.document")
(def ^:private eps 0.01)

(defn- pick [state & ks]
  (some #(let [v (get state %)] (when (some? v) v)) ks))

;; Super-step 1: load document body via store seam
(defn node-load-doc [state]
  (let [doc-id (str/trim (str (or (pick state :doc_id :docId) "")))]
    (if (str/blank? doc-id)
      {:status "error" :error "docId required"}
      (let [app-did (:app-did (audit/config state))
            vertex-id (str "at://" app-did "/" nsid "/" doc-id)]
        (try
          (let [rows (store/select-where "vertex_mangaka" "vertex_id" vertex-id {:limit 1})
                row  (first rows)
                props (when (and row (= "document" (get row "kind"))) (get row "props"))]
            (if (nil? props)
              {:status "error" :error (str "document not found: " doc-id)}
              (let [doc (if (string? props) (json/parse-string props true) (or props {}))]
                {:doc doc :docId doc-id :doc_id doc-id})))
          (catch Exception e
            (let [m (str (.. e getClass getSimpleName) ": " (.getMessage e))]
              {:status "error" :error (subs m 0 (min 300 (count m)))})))))))

;; Super-step 2: walk pages → nodes, normalize
(defn node-parse-nodes [state]
  (let [doc      (or (:doc state) {})
        pages    (or (:pages doc) [])
        page-idx (pick state :page_idx :pageIdx)
        result   (->> (map-indexed vector pages)
                      (keep (fn [[i p]]
                              (when (or (nil? page-idx) (= i (int page-idx)))
                                (let [nodes
                                      (mapv (fn [n]
                                              (let [data (if (map? (:data n)) (:data n) n)]
                                                {:nid (or (:_nid data) (:id n) "")
                                                 :type (or (:type data) (:type n) "")
                                                 :parent (or (:_parent data) "")
                                                 :unit (or (:_unit data) "")
                                                 :x1 (:x1 data) :y1 (:y1 data)
                                                 :x2 (:x2 data) :y2 (:y2 data)
                                                 :imageX (:_imageX data) :imageY (:_imageY data)
                                                 :imageScale (:_imageScale data)
                                                 :hasUrl (boolean (:_genImageUrl data))}))
                                            (or (:nodes p) []))]
                                  {:idx i :name (or (:name p) "") :nodes nodes}))))
                      vec)]
    {:nodes_by_page result :pageCount (count pages)}))

;; Super-step 3: per ai-image, find parent panel, classify rect overlap
(defn node-compute-rect-diff [state]
  (let [pages (or (:nodes_by_page state) [])
        diffs
        (vec
         (mapcat
          (fn [p]
            (let [by-nid (into {} (keep (fn [n] (when (seq (:nid n)) [(:nid n) n])) (:nodes p)))]
              (keep
               (fn [n]
                 (when (= "ai-image" (:type n))
                   (let [parent (get by-nid (or (:parent n) ""))]
                     (if (or (nil? parent) (not= "panel" (:type parent)))
                       {:page (:idx p) :page_name (:name p)
                        :image_nid (:nid n) :panel_nid nil
                        :image_rect [(:x1 n) (:y1 n) (:x2 n) (:y2 n)]
                        :panel_rect nil :status "no_parent_panel"}
                       (let [[ax1 ay1 ax2 ay2] [(:x1 n) (:y1 n) (:x2 n) (:y2 n)]
                             [px1 py1 px2 py2] [(:x1 parent) (:y1 parent) (:x2 parent) (:y2 parent)]]
                         (when (every? some? [ax1 ay1 ax2 ay2 px1 py1 px2 py2])
                           (let [identical (and (< (abs (- ax1 px1)) eps) (< (abs (- ay1 py1)) eps)
                                                (< (abs (- ax2 px2)) eps) (< (abs (- ay2 py2)) eps))
                                 contained (and (>= ax1 (- px1 eps)) (>= ay1 (- py1 eps))
                                                (<= ax2 (+ px2 eps)) (<= ay2 (+ py2 eps)))
                                 status (cond identical "identical_to_panel"
                                              contained "smaller_than_panel"
                                              :else "larger_than_panel")]
                             {:page (:idx p) :page_name (:name p)
                              :image_nid (:nid n) :panel_nid (:nid parent)
                              :image_rect [ax1 ay1 ax2 ay2]
                              :panel_rect [px1 py1 px2 py2]
                              :status status})))))))
               (:nodes p))))
          pages))]
    {:rect_diffs diffs}))

;; Super-step 4: aggregate + emit verdict
(defn node-summarise [state]
  (let [diffs   (or (:rect_diffs state) [])
        pages   (or (:nodes_by_page state) [])
        verbose (boolean (:verbose state))
        total-nodes (reduce + 0 (map #(count (:nodes %)) pages))
        counts-by-type (-> (reduce (fn [m n] (update m (:type n) (fnil inc 0)))
                                   {} (mapcat :nodes pages))
                           (assoc "total" total-nodes))
        bucket (reduce (fn [m d] (update m (:status d) (fnil inc 0)))
                       {"identical_to_panel" 0 "smaller_than_panel" 0
                        "larger_than_panel" 0 "no_parent_panel" 0}
                       diffs)
        per-page (->> diffs
                      (reduce (fn [m d]
                                (let [pg (get m (:page d)
                                              {:idx (:page d) :name (or (:page_name d) "")
                                               :identical 0 :smaller 0 :larger 0 :orphan 0})
                                      k (case (:status d)
                                          "identical_to_panel" :identical
                                          "smaller_than_panel" :smaller
                                          "larger_than_panel" :larger
                                          :orphan)]
                                  (assoc m (:page d) (update pg k inc))))
                              {})
                      vals
                      (sort-by :idx)
                      (mapv (fn [pg]
                              (assoc pg :nodes
                                     (reduce + 0 (map #(count (:nodes %))
                                                      (filter #(= (:idx %) (:idx pg)) pages)))))))
        identical (get bucket "identical_to_panel")
        total-ai  (reduce + 0 (vals bucket))
        verdict (cond
                  (zero? total-ai) "no ai-image nodes found"
                  (= identical total-ai)
                  (str "all " total-ai " ai-images share rect with parent panel. "
                       "selecting inside the shared area picks the ai-image (priority), "
                       "but handles will overlap the panel boundary — they look identical "
                       "until the ai-image rect is resized to differ from panel.")
                  (pos? identical)
                  (str identical "/" total-ai " ai-images still share rect with parent panel; "
                       (get bucket "smaller_than_panel") " have been shrunk inside the panel, "
                       (get bucket "larger_than_panel") " overflow.")
                  :else
                  (str "no ai-images share panel rect. all " total-ai " have been adjusted "
                       "(smaller / larger / orphan)."))]
    {:status "ok"
     :pageCount (or (:pageCount state) (count pages))
     :nodeCounts counts-by-type
     :rectAnalysis bucket
     :perPage per-page
     :samples (if verbose (vec (take 20 diffs)) [])
     :verdict verdict
     :error nil}))

(defn build []
  (-> (g/state-graph)
      (g/add-node :load_doc node-load-doc)
      (g/add-node :parse_nodes node-parse-nodes)
      (g/add-node :compute_rect_diff node-compute-rect-diff)
      (g/add-node :summarise node-summarise)
      (g/add-edge :load_doc :parse_nodes)
      (g/add-edge :parse_nodes :compute_rect_diff)
      (g/add-edge :compute_rect_diff :summarise)
      (g/set-entry-point :load_doc)
      (g/set-finish-point :summarise)
      (g/compile-graph)))

(def GRAPH (build))
