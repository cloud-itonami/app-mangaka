#!/usr/bin/env bb
;; lg-mangaka — bb-native test runner (Clojure / babashka; NOT shell, per the
;; repo rule "Operational code = clj/bb", ADR-2606072802 + 2606280030).
;;
;;   bb --config 60-apps/etzhayyim-project-mangaka/lg-clj/bb.edn test
;;   # or directly (resolves langgraph-clj via the same bb.edn):
;;   bb --config 60-apps/etzhayyim-project-mangaka/lg-clj/bb.edn run_tests.clj
;;
;; Audit emit is disabled here so no background HTTP fires during tests.
(require '[clojure.test :as t])

(when-not (= "1" (System/getenv "LG_AUDIT_DISABLED"))
  (println "note: set LG_AUDIT_DISABLED=1 to silence background audit posts"))

(require 'lg-mangaka.smoke-test)

(let [{:keys [fail error]} (t/run-tests 'lg-mangaka.smoke-test)]
  (if (zero? (+ (or fail 0) (or error 0)))
    (println "── lg-mangaka: ALL suites green ──")
    (do (println "── lg-mangaka: FAILURES above ──")
        (System/exit 1))))
