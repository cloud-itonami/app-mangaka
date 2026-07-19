(ns lg-mangaka.graphs.agent-chat
  "mangaka `agent_chat` graph — manga production AI persona chat.
  NSID: com.etzhayyim.mangaka.chat | .pipelineChat | .projectChat
  Faithful clj port of `lg/lg_mangaka/graphs/agent_chat.py` (ADR-2606280030).

  7 production-stage actors (writer / storyboarder / penciler / inker / toner /
  letterer / colorist), each with a stage-specific system prompt. Forwards one
  user turn to the LLM.

  Topology: START → resolve_actor → llm_call → emit_audit → END.

  DEVIATIONS (noted): (1) langgraph-clj has no RetryPolicy (Python llm_call had
  max_attempts=3); node body is identical, retry wrapper dropped. (2) The LLM
  edge defaults to the Murakumo loopback gateway via `lg-mangaka.llm/*chat*`
  (ADR-2605215000) instead of the RunPod vLLM proxy. The chat call is injectable
  (`*chat*`) so tests verify the prompt assembly + state mapping offline."
  (:require [clojure.string :as str]
            [langgraph.graph :as g]
            [lg-mangaka.llm :as llm]
            [lg-mangaka.audit :as audit]))

(def actor-prompts
  {"writer" (str "You are a manga writer (gensaku-sha) AI. Your job: draft scripts "
                 "(plot beats, dialogue, panel directions). Reply in standard manga "
                 "script format: PAGE/PANEL annotations + dialogue + action notes. "
                 "Cap at 4 pages per turn. Genre-conscious (shōnen / seinen / shōjo / etc.).")
   "storyboarder" (str "You are a manga storyboarder (nemu / name) AI. You break a script "
                       "page into 4-9 panels: shot type, character placement, focal beat. "
                       "Reply with a numbered panel list, 1 line per panel. Note any sfx.")
   "penciler" (str "You are a penciler AI. You describe rough character poses + key "
                   "props for each panel. Reply with structured pose notes: "
                   "Character / Action / Expression / Camera angle. Max 6 panels per turn.")
   "inker" (str "You are an inker AI. You describe line weight + cross-hatching "
                "decisions for a panel. Be concise — 2-3 sentences per panel. "
                "Reference specific elements (face, costume, BG).")
   "toner" (str "You are a screen-toner AI. You suggest tone density + pattern + "
                "placement per panel. Format: 'Panel N: [tone family] @ [%density] "
                "for [region]'. Cap at 6 panels per turn.")
   "letterer" (str "You are a letterer AI. You set balloon shape, tail direction, "
                   "font choice for dialogue. Reply: 'Balloon N: [shape] / [font] / "
                   "tail toward [character] / [position note]'.")
   "colorist" (str "You are a colorist AI. You choose color palettes for cover/key "
                   "art (mostly B/W body, color highlights). Reply with a 3-5 hex "
                   "color palette + 1-line mood justification.")})

(defn system-prompt [role]
  (or (get actor-prompts role) (get actor-prompts "writer")))

;; injectable chat seam (Murakumo loopback default; tests rebind)
(def ^:dynamic *chat* llm/chat)

(defn build-messages
  "Assemble the OpenAI-style messages vector (mirrors agent_chat._node_llm_call)."
  [{:keys [actor_role work_id chapter_id page_id message history]}]
  (let [role   (or actor_role "writer")
        ctx    (cond-> []
                 (seq (str work_id))    (conj (str "work_id=" work_id))
                 (seq (str chapter_id)) (conj (str "chapter_id=" chapter_id))
                 (seq (str page_id))    (conj (str "page_id=" page_id)))
        system (cond-> (system-prompt role)
                 (seq ctx) (str "\n\nContext: " (str/join ", " ctx) "."))
        hist   (->> (or history [])
                    (take-last 12)
                    (keep (fn [h]
                            (when (map? h)
                              (let [r (or (get h :role) (get h "role"))
                                    c (or (get h :content) (get h "content"))]
                                (when (and (#{"user" "assistant"} r) (string? c) (seq c))
                                  {:role r :content (subs c 0 (min 2000 (count c)))}))))))]
    (-> [{:role "system" :content system}]
        (into hist)
        (conj {:role "user" :content (subs message 0 (min 4000 (count message)))}))))

(defn node-resolve-actor [state]
  (let [role (or (:actor_role state) "writer")
        app-did (:app-did (audit/config state))]
    {:actor_did (str app-did ":actor:" role) :actor_role role}))

(defn node-llm-call [state]
  (let [user-text (str/trim (or (:message state) ""))]
    (if (str/blank? user-text)
      {:error "message required"}
      (let [messages (build-messages (assoc state :message user-text))
            res (*chat* messages {:max-tokens (int (or (:max_tokens state) 512))
                                  :host-config (:llm (audit/config state))
                                  :temperature (double (or (:temperature state) 0.7))})]
        (if (:error res)
          {:error (:error res)}
          {:reply (:reply res)
           :model (:model res)
           :prompt_tokens (:prompt_tokens res)
           :completion_tokens (:completion_tokens res)
           :total_tokens (:total_tokens res)})))))

(defn node-emit-audit [state]
  (audit/emit-audit-bg
   state
   {:actor (or (:actor_did state) (:app-did (audit/config state)))
    :activity "mangaka.chat.reply"
    :object-id (str "chat:" (or (:actor_role state) "writer") ":"
                    (quot (System/currentTimeMillis) 1000))
    :object-type "mangaka.chat"
    :attributes {:actorRole (:actor_role state)
                 :workId (:work_id state)
                 :userDid (:user_did state)
                 :totalTokens (:total_tokens state 0)
                 :model (:model state)
                 :ok (not (boolean (:error state)))}})
  {})

(defn build []
  (-> (g/state-graph)
      (g/add-node :resolve_actor node-resolve-actor)
      (g/add-node :llm_call node-llm-call)
      (g/add-node :emit_audit node-emit-audit)
      (g/add-edge :resolve_actor :llm_call)
      (g/add-edge :llm_call :emit_audit)
      (g/set-entry-point :resolve_actor)
      (g/set-finish-point :emit_audit)
      (g/compile-graph)))

(def GRAPH (build))
