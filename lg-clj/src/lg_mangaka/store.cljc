(ns lg-mangaka.store
  "Document / vertex store — the charter-clean replacement for the Python
  RisingWave + psycopg + `kotodama.kotoba_datomic` persistence
  (substrate boundary: NO RisingWave/Postgres; state belongs on the kotoba
  Datom log, ADR-2605262130 / 2605312345). This namespace is the swap seam:
  the default backend is an in-process append-only atom of `vertex_mangaka`
  rows keyed by vertex-id (EAVT-shaped row maps) + an edge log, which the
  kotoba Datom-log adapter can replace without touching the graphs.

  Faithful-default gate: like the Python (which skips persistence / errors
  when RW_URL is unset), the store is a NO-OP unless MANGAKA_STORE_ENABLED=1
  (or RW_URL is set as a legacy signal). Enabled, it is a real end-to-end
  store — strictly more functional than the unconfigured Python path.

  Mirrors the `get_kotoba_client()` surface the Python nodes used:
    insert-row!    ≙ client.insert_row(table, row)
    select-where   ≙ client.select_where(table, col, val, columns, limit)
    q              ≙ client.q(datalog)   (health probe)"
  (:require [clojure.string :as str]))

(def ^:dynamic *enabled?* false)

(defn enabled?
  "True when the store should persist (faithful analogue of Python's RW_URL gate)."
  [] (boolean *enabled?*))

;; append-only in-process backend: table -> (vector of row maps)
(defonce ^:private db (atom {}))

(defn reset-store! [] (reset! db {}))

(defn now-iso []
  (.format (java.time.format.DateTimeFormatter/ofPattern "yyyy-MM-dd'T'HH:mm:ss'Z'")
           (java.time.ZonedDateTime/now (java.time.ZoneOffset/UTC))))

;; ── injectable client seam ──────────────────────────────────────────────────
;; Graphs talk to the store ONLY through these dynamic vars; the kotoba
;; Datom-log adapter (or a test stub) rebinds them. Defaults hit the in-process
;; atom above when enabled, else behave like the unconfigured Python path.

(defn default-insert-row!
  "INSERT analogue. Returns {:inserted bool}. No-op when disabled."
  [table row]
  (if-not (enabled?)
    {:inserted false}
    (do (swap! db update table (fnil conj []) row)
        {:inserted true})))

(defn default-select-where
  "SELECT analogue: rows in `table` where (= (get row col) val), newest-first by
  created_at, capped at limit. Returns a vector of row maps (possibly empty)."
  [table col val {:keys [limit] :or {limit 50}}]
  (if-not (enabled?)
    []
    (->> (get @db table [])
         (filter #(= val (get % col)))
         (sort-by #(or (get % "created_at") "") #(compare %2 %1))
         (take limit)
         vec)))

(def ^:dynamic *insert-row!*   default-insert-row!)
(def ^:dynamic *select-where*  default-select-where)
(def ^:dynamic *q*
  "Datalog probe seam (health). Default: throws unless enabled, mirroring the
  Python get_kotoba_client().q(...) which fails when RW is unreachable."
  (fn [_datalog]
    (if (enabled?)
      []
      (throw (ex-info "kotoba store not configured (set MANGAKA_STORE_ENABLED=1 or RW_URL)"
                      {:store :unconfigured})))))

;; convenience wrappers (keep call sites readable + indirection through vars)
(defn insert-row! [table row] (*insert-row!* table row))
(defn select-where
  ([table col val] (select-where table col val {}))
  ([table col val opts] (*select-where* table col val opts)))
(defn q [datalog] (*q* datalog))
