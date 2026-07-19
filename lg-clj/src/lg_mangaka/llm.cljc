(ns lg-mangaka.llm
  "LLM helpers for lg-mangaka graphs — clj port of `lg/lg_mangaka/llm.py` +
  the chat path in `agent_chat.py` (ADR-2606280030).

  DEVIATION (noted): the Python posts to a RunPod-served vLLM proxy URL. Per
  ADR-2605215000 / 2606172359 (Murakumo DEFAULT-PREFERRED, read-only, no
  server key) the LLM edge here defaults to the Murakumo loopback gateway
  (LiteLLM, OpenAI-compatible /v1/chat/completions on 127.0.0.1:4000) over
  babashka.http-client and asserts the endpoint is on the Murakumo fleet
  allowlist. Both helpers are defensive: failures return nil/{:error ...}
  rather than raising, so the Pregel pipeline keeps moving with a
  deterministic fallback (matching the Python try/except).

  `chat`     — single-turn chat completion → text or {:error ...}.
  `llm-json` — chat completion parsed as JSON (tolerates ```json fences and
               leading prose) → map or nil."
  (:require [cheshire.core :as json]
            [clojure.string :as str]))

;; Murakumo fleet (ADR-2605215000) — the ONLY inference endpoints representable.
(def murakumo-allowed-hosts
  #{"127.0.0.1:4000" "localhost:4000"
    "192.168.1.70:8077" "192.168.1.70:11434"
    "127.0.0.1:11434" "localhost:11434"})

(def default-config {:url "http://127.0.0.1:4000/v1"
                     :model "gemma3:4b" :timeout-ms 60000})
(def ^:dynamic *http-post* nil)

(defn- clip [s n] (let [s (str s)] (subs s 0 (min n (count s)))))

(defn assert-murakumo
  "Refuse any LLM endpoint outside the Murakumo fleet (http only). Returns nil
  when allowed; throws ex-info otherwise."
  [endpoint]
  (when-let [[_ scheme host] (re-find #"^([A-Za-z][A-Za-z0-9+.\-]*)://([^/?#]*)" (str endpoint))]
    (when-not (and (= "http" (str/lower-case scheme))
                   (contains? murakumo-allowed-hosts (str/lower-case host)))
      (throw (ex-info (str "inference endpoint " (pr-str endpoint)
                           " is outside the Murakumo fleet (ADR-2605215000)")
                      {:murakumo-only-violation true :endpoint endpoint})))))

(defn chat
  "Single-turn chat. `messages` is a vector of {:role :content} maps. Returns
  {:reply <text> :model <m> :prompt_tokens .. :completion_tokens ..
   :total_tokens ..} or {:error ...}. Mirrors agent_chat._node_llm_call."
  [messages {:keys [max-tokens temperature host-config]
             :or {max-tokens 512 temperature 0.7}}]
  (when-not (fn? *http-post*)
    (throw (ex-info "Mangaka inference requires an explicit HTTP POST capability"
                    {:capability :mangaka/llm-http-post})))
  (let [{:keys [url model timeout-ms]} (merge default-config (or host-config {}))
        url (str/replace url #"/+$" "")]
  (try
    (assert-murakumo url)
    (let [resp (*http-post* (str url "/chat/completions")
                          {:headers {"Content-Type" "application/json"}
                           :timeout timeout-ms
                           :throw false
                           :body (json/generate-string
                                  {:model model :messages messages
                                   :max_tokens max-tokens :temperature temperature})})
          status (:status resp)]
      (if (>= status 400)
        {:error (str "vllm http " status ": " (clip (:body resp) 200))}
        (let [body   (json/parse-string (:body resp) true)
              choice (get-in body [:choices 0] {})
              msg    (get choice :message {})
              usage  (get body :usage {})]
          {:reply (str/trim (str (:content msg)))
           :model (or (:model body) model)
           :prompt_tokens (int (or (:prompt_tokens usage) 0))
           :completion_tokens (int (or (:completion_tokens usage) 0))
           :total_tokens (int (or (:total_tokens usage) 0))})))
    (catch Exception e {:error (clip (.getMessage e) 200)}))))

(defn parse-json-loose
  "Parse JSON tolerating ```json fences and trailing prose. Returns a map or nil.
  Mirrors llm.py:_parse_json_loose."
  [text]
  (when (seq (str text))
    (let [fenced    (re-find #"(?s)```(?:json)?\s*(\{.*?\})\s*```" text)
          candidate (if fenced (second fenced) (str/trim text))
          candidate (if (str/starts-with? candidate "{")
                      candidate
                      (let [i (str/index-of candidate "{")
                            j (str/last-index-of candidate "}")]
                        (if (and i j (> j i)) (subs candidate i (inc j)) candidate)))]
      (try
        (let [out (json/parse-string candidate true)]
          (when (map? out) out))
        (catch Exception _ nil)))))

(defn llm-json
  "Call the Murakumo chat endpoint and parse the reply as JSON. Returns a map or
  nil. Mirrors llm.py:llm_json (response_format json_object)."
  ([system user] (llm-json system user nil))
  ([system user host-config]
   (let [res (chat [{:role "system" :content system}
                    {:role "user" :content user}]
                   {:max-tokens 1024 :temperature 0.4
                    :host-config host-config})]
     (when-not (:error res)
       (parse-json-loose (:reply res))))))
