(ns lg-mangaka.smoke-test
  "Smoke tests for the lg-mangaka clj port — clojure.test analogue of the Python
  `tests/test_smoke.py`, plus node-behaviour tests the original could not run
  offline (the store + LLM are injectable here, so persistence + transforms +
  the canvas-rect analysis verify under bb with stubs)."
  (:require [clojure.test :refer [deftest is testing use-fixtures]]
            [langgraph.graph :as g]
            [lg-mangaka.server :as server]
            [lg-mangaka.store :as store]
            [lg-mangaka.llm :as llm]
            [lg-mangaka.graphs.health :as health]
            [lg-mangaka.graphs.agent-chat :as chat]
            [lg-mangaka.graphs.save-document :as save-doc]
            [lg-mangaka.graphs.load-document :as load-doc]
            [lg-mangaka.graphs.list-documents :as list-docs]
            [lg-mangaka.graphs.record-op-log :as oplog]
            [lg-mangaka.graphs.debug-canvas-state :as canvas]))

(def expected-graphs
  #{"health" "agent_chat" "save_document" "load_document"
    "list_documents" "record_op_log" "debug_canvas_state"})

(def expected-nsid-map
  {"com.etzhayyim.mangaka.health"          "health"
   "com.etzhayyim.mangaka.chat"            "agent_chat"
   "com.etzhayyim.mangaka.pipelineChat"    "agent_chat"
   "com.etzhayyim.mangaka.projectChat"     "agent_chat"
   "com.etzhayyim.mangaka.saveDocument"    "save_document"
   "com.etzhayyim.mangaka.loadDocument"    "load_document"
   "com.etzhayyim.mangaka.listDocuments"   "list_documents"
   "com.etzhayyim.mangaka.recordOpLog"     "record_op_log"
   "com.etzhayyim.mangaka.debugCanvasState" "debug_canvas_state"})

(use-fixtures :each (fn [t] (store/reset-store!) (t) (store/reset-store!)))

;; ── server registry parity (mirrors test_smoke.py) ──────────────────────────

(deftest graphs-match-expected-set
  (is (= expected-graphs (set (keys server/GRAPHS)))))

(deftest nsid-map-completeness
  (testing "every non-health ported graph has at least one NSID"
    (let [mapped (set (vals server/NSID-MAP))]
      (doseq [gname (disj expected-graphs "health")]
        (is (contains? mapped gname) (str gname " has no NSID mapping")))))
  (is (= expected-nsid-map server/NSID-MAP)))

(deftest nsid-map-references-known-graphs
  (doseq [[nsid gname] server/NSID-MAP]
    (is (contains? server/GRAPHS gname) (str nsid " → " gname " not in GRAPHS"))))

(deftest all-graphs-invocable
  (doseq [[nm graph] server/GRAPHS]
    (is (some? graph) (str "GRAPHS[" nm "] nil"))))

;; ── dispatch surface (/ok, /runs, /xrpc) ────────────────────────────────────

(deftest ok-endpoint-lists-graphs
  (let [r (server/health)]
    (is (= 200 (:status r)))
    (is (true? (get-in r [:body :ok])))
    (is (= expected-graphs (set (get-in r [:body :graphs]))))))

(deftest unknown-assistant-404
  (is (= 404 (:status (server/dispatch-run {:assistant_id "nope" :input {}})))))

(deftest unknown-nsid-404
  (is (= 404 (:status (server/dispatch-xrpc "com.etzhayyim.mangaka.unknownMethod" {})))))

(deftest api-key-guard
  (testing "no key configured → pass"
    (is (nil? (server/check-api-key ""))))
  (testing "configured key mismatch → 401"
    (is (= 401 (:status (server/dispatch-run {:assistant_id "health"}
                                             {:api-key "secret"
                                              :x-api-key "wrong"}))))))

(deftest dispatch-propagates-explicit-host-config
  (let [custom-did "did:web:custom.example"
        out (server/dispatch-run
             {:assistant_id "save_document"
              :input {:docId "explicit-host" :document "{}"}}
             {:host-config {:app-did custom-did :store-enabled? true}})]
    (is (= 200 (:status out)))
    (is (= (str "at://" custom-did "/com.etzhayyim.mangaka.document/explicit-host")
           (get-in out [:body :result :vertexId])))
    (binding [store/*enabled?* true]
      (is (= custom-did
             (get (first (store/select-where "vertex_mangaka" "rkey" "explicit-host"
                                            {:limit 1}))
                  "owner_did"))))))

;; ── health graph end-to-end ─────────────────────────────────────────────────

(deftest health-graph-ok-with-store
  (with-redefs [store/enabled? (constantly true)]
    (let [out (g/invoke health/GRAPH {})]
      (is (true? (:ok out)))
      (is (true? (:rw_ok out)))
      (is (string? (:server_now out))))))

(deftest health-graph-degrades-without-store
  (with-redefs [store/enabled? (constantly false)]
    (let [out (g/invoke health/GRAPH {})]
      (is (false? (:ok out)))
      (is (false? (:rw_ok out)))
      (is (re-find #"rw:" (:error out))))))

;; ── agent_chat graph: prompt assembly + LLM stub + actor resolution ─────────

(deftest agent-chat-system-prompt-roles
  (is (re-find #"storyboarder" (chat/system-prompt "storyboarder")))
  (is (re-find #"writer" (chat/system-prompt "writer")))
  (is (= (chat/system-prompt "writer") (chat/system-prompt "nonexistent-role"))
      "unknown role falls back to writer"))

(deftest agent-chat-builds-messages-with-context
  (let [msgs (chat/build-messages {:actor_role "inker" :work_id "w1" :page_id "p3"
                                   :message "ink this"
                                   :history [{:role "user" :content "hi"}
                                             {:role "assistant" :content "ok"}
                                             {:role "system" :content "drop me"}]})]
    (is (= "system" (:role (first msgs))))
    (is (re-find #"work_id=w1" (:content (first msgs))))
    (is (re-find #"page_id=p3" (:content (first msgs))))
    (is (= "ink this" (:content (last msgs))))
    (is (= 4 (count msgs)) "system + 2 valid history + user; system-role history dropped")))

(deftest agent-chat-empty-message-errors
  (let [out (g/invoke chat/GRAPH {:message "  "})]
    (is (= "message required" (:error out)))))

(deftest agent-chat-happy-path-stubbed
  (binding [chat/*chat* (fn [_msgs _opts]
                          {:reply "PAGE 1 PANEL 1: ..." :model "gemma3:4b"
                           :prompt_tokens 10 :completion_tokens 20 :total_tokens 30})]
    (let [out (g/invoke chat/GRAPH {:actor_role "writer" :message "draft a fight scene"})]
      (is (= "did:web:mangaka.etzhayyim.com:actor:writer" (:actor_did out)))
      (is (re-find #"PAGE 1" (:reply out)))
      (is (= 30 (:total_tokens out))))))

(deftest agent-chat-llm-error-propagates
  (binding [chat/*chat* (fn [_ _] {:error "vllm http 500: boom"})]
    (let [out (g/invoke chat/GRAPH {:message "hi"})]
      (is (re-find #"boom" (:error out)))
      (is (nil? (:reply out))))))

;; ── murakumo fleet guard (ADR-2605215000) ───────────────────────────────────

(deftest murakumo-guard
  (testing "off-fleet endpoint refused"
    (is (thrown? clojure.lang.ExceptionInfo (llm/assert-murakumo "https://api.openai.com/v1"))))
  (testing "loopback gateway allowed"
    (is (nil? (llm/assert-murakumo "http://127.0.0.1:4000/v1")))))

(deftest llm-http-capability-is-explicit-and-fail-closed
  (is (thrown-with-msg? clojure.lang.ExceptionInfo #"explicit HTTP POST capability"
                        (llm/chat [] {})))
  (let [seen (atom nil)]
    (binding [llm/*http-post*
              (fn [url request]
                (reset! seen [url request])
                {:status 200
                 :body "{\"model\":\"safe-local\",\"choices\":[{\"message\":{\"content\":\"ok\"}}]}"})]
      (is (= "ok" (:reply (llm/chat [] {:host-config {:model "safe-local"}}))))
      (is (= "http://127.0.0.1:4000/v1/chat/completions" (first @seen))))))

(deftest parse-json-loose-handles-fences
  (is (= {:a 1} (llm/parse-json-loose "here you go ```json\n{\"a\": 1}\n``` done")))
  (is (= {:b 2} (llm/parse-json-loose "prefix {\"b\": 2} suffix")))
  (is (nil? (llm/parse-json-loose "not json at all"))))

;; ── save / load / list document graphs (store seam) ─────────────────────────

(deftest save-document-requires-docid
  (let [out (g/invoke save-doc/GRAPH {})]
    (is (= "error" (:status out)))
    (is (= "docId required" (:error out)))))

(deftest save-then-load-roundtrip
  (with-redefs [store/enabled? (constantly true)]
    (let [saved (g/invoke save-doc/GRAPH {:docId "doc-gh-arc0-1-origin"
                                          :name "Origin" :document "{\"pages\":[]}"})]
      (is (= "saved" (:status saved)))
      (is (= "at://did:web:mangaka.etzhayyim.com/com.etzhayyim.mangaka.document/doc-gh-arc0-1-origin"
             (:vertexId saved)))
      (let [loaded (g/invoke load-doc/GRAPH {:docId "doc-gh-arc0-1-origin"})]
        (is (= "Origin" (:name loaded)))
        (is (= "{\"pages\":[]}" (:document loaded)))
        (is (nil? (:error loaded)))))))

(deftest load-document-not-configured
  (with-redefs [store/enabled? (constantly false)]
    (is (= "RW_URL not configured" (:error (g/invoke load-doc/GRAPH {:docId "x"}))))))

(deftest load-document-not-found
  (with-redefs [store/enabled? (constantly true)]
    (is (re-find #"not found" (:error (g/invoke load-doc/GRAPH {:docId "ghost"}))))))

(deftest list-documents-clamps-and-paginates
  (with-redefs [store/enabled? (constantly true)]
    (dotimes [i 3]
      (g/invoke save-doc/GRAPH {:docId (str "doc-" i) :name (str "D" i)
                                :document "{}"}))
    (let [out (g/invoke list-docs/GRAPH {:limit 9999 :offset 0})]
      (is (= 200 (:limit out)) "limit clamped to 200")
      (is (= 0 (:offset out)))
      (is (= 3 (:total out)))
      (is (= 3 (count (:items out))))
      (is (every? :docId (:items out))))))

(deftest list-documents-empty-when-disabled
  (with-redefs [store/enabled? (constantly false)]
    (let [out (g/invoke list-docs/GRAPH {})]
      (is (= [] (:items out)))
      (is (= 0 (:total out))))))

;; ── record_op_log graph (2-step build → write) ──────────────────────────────

(deftest record-op-log-requires-docid
  (let [out (g/invoke oplog/GRAPH {})]
    (is (= "error" (:status out)))
    (is (= "docId required" (:error out)))))

(deftest record-op-log-short-nid
  (is (= "anon" (oplog/short-nid "")))
  (is (= "abcdefghij" (oplog/short-nid "nnabcdefghijKLMNOP"))))

(deftest record-op-log-writes-vertex-and-edge
  (with-redefs [store/enabled? (constantly true)]
    (let [out (g/invoke oplog/GRAPH {:doc_id "doc-1" :op "move" :nid "nABC"
                                     :node_type "panel"})]
      (is (= "recorded" (:status out)))
      (is (re-find #"^op-doc-1-" (:rkey out)))
      ;; one opLog vertex + one emits_op edge persisted
      (let [verts (store/select-where "vertex_mangaka" "kind" "opLog" {:limit 10})
            edges (store/select-where "edge_mangaka_emits_op" "owner_did"
                                      "did:web:mangaka.etzhayyim.com" {:limit 10})]
        (is (= 1 (count verts)))
        (is (= 1 (count edges)))))))

;; ── debug_canvas_state graph: 4-step rect analysis (pure, offline) ──────────

(def ^:private sample-doc
  {:pages
   [{:name "p0"
     :nodes
     [{:data {:_nid "panel1" :type "panel" :x1 0 :y1 0 :x2 100 :y2 100}}
      ;; identical to parent panel
      {:data {:_nid "img1" :type "ai-image" :_parent "panel1"
              :x1 0 :y1 0 :x2 100 :y2 100 :_genImageUrl "u"}}
      {:data {:_nid "panel2" :type "panel" :x1 0 :y1 0 :x2 200 :y2 200}}
      ;; smaller / contained
      {:data {:_nid "img2" :type "ai-image" :_parent "panel2"
              :x1 10 :y1 10 :x2 50 :y2 50}}
      ;; orphan (no parent panel)
      {:data {:_nid "img3" :type "ai-image" :_parent "missing"
              :x1 0 :y1 0 :x2 10 :y2 10}}]}]})

(deftest debug-canvas-requires-docid
  ;; Faithful to the Python: load_doc's guard returns {:status "error"} but the
  ;; graph edges are linear (load_doc → ... → summarise) so summarise always
  ;; runs and overwrites :status to "ok" with an empty-doc verdict. We assert
  ;; the guard at the NODE level (the load_doc step itself), which is where the
  ;; "docId required" error is faithfully produced.
  (is (= "error" (:status (canvas/node-load-doc {}))))
  (is (= "docId required" (:error (canvas/node-load-doc {}))))
  (let [out (g/invoke canvas/GRAPH {})]
    (is (= "ok" (:status out)) "graph runs to completion (no short-circuit, faithful)")
    (is (= "no ai-image nodes found" (:verdict out)))))

(deftest debug-canvas-full-analysis
  (with-redefs [store/enabled? (constantly true)]
    ;; seed the doc directly through the store seam (kind=document)
    (store/insert-row! "vertex_mangaka"
                       {"vertex_id" "at://did:web:mangaka.etzhayyim.com/com.etzhayyim.mangaka.document/doc-x"
                        "kind" "document"
                        "props" (cheshire.core/generate-string sample-doc)})
    (let [out (g/invoke canvas/GRAPH {:docId "doc-x" :verbose true})]
      (is (= "ok" (:status out)))
      (is (= 1 (:pageCount out)))
      (is (= 1 (get-in out [:rectAnalysis "identical_to_panel"])))
      (is (= 1 (get-in out [:rectAnalysis "smaller_than_panel"])))
      (is (= 1 (get-in out [:rectAnalysis "no_parent_panel"])))
      (is (= 3 (get-in out [:nodeCounts "ai-image"])))
      (is (= 2 (get-in out [:nodeCounts "panel"])))
      (is (= 3 (count (:samples out))))
      (is (string? (:verdict out))))))

(deftest debug-canvas-no-aimage-verdict
  (with-redefs [store/enabled? (constantly true)]
    (store/insert-row! "vertex_mangaka"
                       {"vertex_id" "at://did:web:mangaka.etzhayyim.com/com.etzhayyim.mangaka.document/doc-empty"
                        "kind" "document"
                        "props" (cheshire.core/generate-string {:pages [{:name "p0" :nodes []}]})})
    (let [out (g/invoke canvas/GRAPH {:docId "doc-empty"})]
      (is (= "ok" (:status out)))
      (is (= "no ai-image nodes found" (:verdict out))))))
