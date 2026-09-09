;; ported from 60-apps/etzhayyim-project-mangaka/lg/lg_mangaka/graphs/mangaka_generate_page.py
;; real 1:1 port replacing the unit_refactor stage-0 "TODO: port-failed" stub.
;; NS fixed (root.* prefix removed) and the file is now .cljc.
;; Self-contained: no sibling stub requires; the comfy_runner / comfy_workflows /
;; PIL dependencies are injected as fns so the pure logic ports faithfully.
;;
;; mangaka_generate_page — character-stable page from a panel layout.
;;   1. plan       — validate the page record + upload the character ref
;;   2. render     — for each panel, build workflow → submit → poll → inked image
;;   3. composite  — PIL composite onto a manga page canvas via each panel bbox
;;
;; House style: state/panels stay string-keyed (the shapes Python dicts produced);
;; Python ':kw' strings are kept AS strings; pure fns; image/host I/O is behind
;; #?(:clj ...) / injected. The langgraph module-level compile is omitted; `build-graph`
;; returns the node/edge topology as data so the graph shape is faithful.
(ns lg.lg-mangaka.graphs.mangaka-generate-page
  (:require [clojure.string :as str]))

;; ── pure helpers ──────────────────────────────────────────────────────────────
(defn merge-list
  "_merge_list(a, b) — list(a or []) + list(b or [])."
  [a b]
  (vec (concat (or a []) (or b []))))

(defn- now-ms []
  #?(:clj (System/currentTimeMillis) :default 0))

(defn- b64-encode-ascii [^bytes bs]
  #?(:clj (.encodeToString (java.util.Base64/getEncoder) bs)
     :default (throw (ex-info "bind a base64 encoder on this host" {}))))

(defn- b64-decode ^bytes [^String s]
  #?(:clj (.decode (java.util.Base64/getDecoder) s)
     :default (throw (ex-info "bind a base64 decoder on this host" {}))))

(defn- to-long [v] (long (or v 0)))
(defn- to-double [v] (double (or v 0.0)))

;; ── plan: validate + upload character ref ─────────────────────────────────────
;; async def _plan(state) — validate, then upload_image_b64 the reference.
;; `upload-image-b64` : (b64 comfy-url filename-hint image-mime) -> {"filename" .. "error" ..}
;; `default-url` is the comfy_runner.DEFAULT_URL string.
(defn plan
  "_plan(state). Validates reference + panels, uploads the character reference.
  Returns a state-fragment map (string-keyed)."
  [state upload-image-b64 default-url]
  (cond
    (not (get state "reference_image_b64"))
    {"status" "error" "error" "reference_image_b64 required"}

    (empty? (or (get state "panels") []))
    {"status" "error" "error" "panels (list) required"}

    :else
    (let [started (now-ms)
          up (upload-image-b64
              (get state "reference_image_b64")
              (or (get state "comfy_url") default-url)
              (str "mangaka-page-" (or (get state "page_rkey") "page"))
              (or (get state "reference_image_mime") "image/png"))]
      (if (get up "error")
        {"status" "error" "error" (get up "error")}
        {"uploaded_filename" (get up "filename")
         "started_at_ms" started}))))

;; ── fan-out + per-panel render (sequential through ComfyUI queue) ──────────────
;; async def _render_all_panels(state) — build → submit → poll per panel.
;; panel-stable-workflow : (opts-map) -> workflow
;; submit-workflow       : (wf comfy-url) -> {"status".. "prompt_id".. "started_at_ms".. "error"..}
;; poll-outputs          : (prompt-id comfy-url started-at-ms timeout-seconds) -> {"images".. "elapsed_ms".. "error"..}
(defn render-all-panels
  "_render_all_panels(state). Renders each panel and prefers the inked output
  (node '13') over the composition. Faithful to the Python loop."
  [state default-url panel-stable-workflow submit-workflow poll-outputs]
  (if (= (get state "status") "error")
    {}
    (let [panels (or (get state "panels") [])
          comfy-url (or (get state "comfy_url") default-url)
          seed-base (let [s (to-long (get state "seed_base"))]
                      (if (zero? s) #?(:clj (quot (System/currentTimeMillis) 1000) :default 0) s))
          panel-results
          (reduce
           (fn [acc [i p]]
             (let [started (now-ms)
                   default-rkey (format "p%02d" (long i))
                   wf (panel-stable-workflow
                       {"panel_rkey" (str (or (get p "panel_rkey") default-rkey))
                        "reference_image_filename" (get state "uploaded_filename")
                        "framing" (or (get p "framing") "medium")
                        "characters" (vec (or (get p "characters") []))
                        "environment" (or (get p "environment") "")
                        "mood" (or (get p "mood") "")
                        "action" (or (get p "action") "")
                        "base_denoise" (let [d (to-double (get state "base_denoise"))] (if (zero? d) 0.65 d))
                        "refine_denoise" (let [d (to-double (get state "refine_denoise"))] (if (zero? d) 0.4 d))
                        "seed" (bit-and (+ seed-base (* i 1009)) 0xFFFFFFFF)})
                   sub (submit-workflow wf comfy-url)]
               (if (= (get sub "status") "error")
                 (conj acc {"panel_rkey" (or (get p "panel_rkey") default-rkey)
                            "ok" false "error" (get sub "error")})
                 (let [poll (poll-outputs
                             (get sub "prompt_id") comfy-url
                             (get sub "started_at_ms" started)
                             (to-long (or (get state "timeout_seconds") 300)))
                       imgs (or (get poll "images") [])
                       inked (or (some (fn [im] (when (= (get im "node") "13") im)) imgs)
                                 (when (seq imgs) (last imgs)))]
                   (conj acc {"panel_rkey" (or (get p "panel_rkey") default-rkey)
                              "x" (to-long (get p "x")) "y" (to-long (get p "y"))
                              "w" (to-long (get p "w")) "h" (to-long (get p "h"))
                              "ok" (boolean inked)
                              "image_b64" (or (get (or inked {}) "imageInlineB64") "")
                              "image_mime" (or (get (or inked {}) "imageMime") "image/png")
                              "latency_ms" (or (get poll "elapsed_ms") 0)
                              "error" (get poll "error")})))))
           []
           (map-indexed vector panels))]
      {"panel_results" panel-results})))

;; ── composite geometry (PURE) ─────────────────────────────────────────────────
;; The PIL resize/crop/paste math, extracted pure so it ports faithfully and is
;; testable without an image library. Mirrors _composite_blocking's per-panel block.
(defn panel-placement
  "Pure geometry for one panel: given source (img-w, img-h) and bbox/gutter/border,
  returns the resize target, crop offsets and paste origin — faithful to the Python.
  Returns nil for a skipped panel (bw<=0 or bh<=0)."
  [{:strs [x y w h]} img-w img-h gutter border]
  (let [bw (to-long w) bh (to-long h)
        bx (to-long x) by (to-long y)]
    (when (and (> bw 0) (> bh 0))
      (let [target-w (max 1 (- bw gutter))
            target-h (max 1 (- bh gutter))
            src-ratio (/ (double img-w) (double img-h))
            dst-ratio (/ (double target-w) (double target-h))
            ;; python: if src>dst -> new_h=target_h, new_w=round(target_h*src)
            ;;         else        -> new_w=target_w, new_h=round(target_w/src)
            new-w (if (> src-ratio dst-ratio) (long (Math/round (* (double target-h) src-ratio))) target-w)
            new-h (if (> src-ratio dst-ratio) target-h (long (Math/round (/ (double target-w) src-ratio))))
            ox (quot (- new-w target-w) 2)
            oy (quot (- new-h target-h) 2)
            paste-x (+ bx (quot gutter 2))
            paste-y (+ by (quot gutter 2))]
        {"target_w" target-w "target_h" target-h
         "resize_w" new-w "resize_h" new-h
         "crop_ox" ox "crop_oy" oy
         "paste_x" paste-x "paste_y" paste-y
         "border" border}))))

;; ── composite onto a page canvas via PIL ──────────────────────────────────────
;; def _composite_blocking(state) -> (b64, err). PIL is injected via `image-ops`,
;; a map of fns {:new :decode :resize :crop :paste :rectangle :encode-png}; if nil,
;; PIL is unavailable -> ("", "PIL not available").
(defn composite-blocking
  "_composite_blocking(state) -> [b64 err]. Composites inked panels onto a white
  page canvas using panel-placement geometry. `image-ops` injects the image backend."
  [state image-ops]
  (if (nil? image-ops)
    ["" "PIL not available: no image backend bound"]
    (let [panels (or (get state "panel_results") [])
          w (let [v (to-long (get state "page_width"))] (if (zero? v) 1280 v))
          h (let [v (to-long (get state "page_height"))] (if (zero? v) 1817 v))
          gutter (let [v (to-long (get state "gutter"))] (if (zero? v) 12 v))
          border (let [v (to-long (get state "border"))] (if (zero? v) 2 v))
          new-canvas (get image-ops :new)
          decode (get image-ops :decode)
          resize (get image-ops :resize)
          crop (get image-ops :crop)
          paste (get image-ops :paste)
          rectangle (get image-ops :rectangle)
          encode-png (get image-ops :encode-png)
          canvas (new-canvas w h "white")]
      (doseq [pr panels]
        (when (and (get pr "ok") (not= "" (or (get pr "image_b64") "")))
          (when-let [img (try (decode (b64-decode (get pr "image_b64")))
                              (catch #?(:clj Exception :default :default) _ nil))]
            (let [iw (get img "width") ih (get img "height")]
              (when-let [place (panel-placement pr iw ih gutter border)]
                (let [img2 (resize img (get place "resize_w") (get place "resize_h"))
                      img3 (crop img2 (get place "crop_ox") (get place "crop_oy")
                                 (+ (get place "crop_ox") (get place "target_w"))
                                 (+ (get place "crop_oy") (get place "target_h")))]
                  (paste canvas img3 (get place "paste_x") (get place "paste_y"))
                  (when (> border 0)
                    (rectangle canvas
                               (get place "paste_x") (get place "paste_y")
                               (+ (get place "paste_x") (get place "target_w") -1)
                               (+ (get place "paste_y") (get place "target_h") -1)
                               "black" border))))))))
      [(b64-encode-ascii (encode-png canvas)) nil])))

;; async def _composite(state) — to_thread(_composite_blocking) + elapsed bookkeeping.
(defn composite
  "_composite(state). Runs the blocking composite and stamps elapsed_ms."
  [state image-ops]
  (if (= (get state "status") "error")
    {}
    (let [[b64 err] (composite-blocking state image-ops)
          elapsed (- (now-ms) (to-long (get state "started_at_ms")))]
      (if err
        {"status" "error" "error" err "elapsed_ms" elapsed}
        {"page_image_inline_b64" b64
         "status" "ok"
         "elapsed_ms" elapsed}))))

;; ── build graph ───────────────────────────────────────────────────────────────
;; def _build_graph() — plan (retry max_attempts=2) → render → composite.
;; langgraph compile omitted; topology returned as data.
(defn build-graph
  "_build_graph() — returns the mangaka_generate_page topology as a data spec
  (nodes + retry policy + edges). The langgraph runtime compile is omitted."
  []
  {"nodes" [{"name" "plan" "fn" "plan" "retry_policy" {"max_attempts" 2}}
            {"name" "render" "fn" "render-all-panels"}
            {"name" "composite" "fn" "composite"}]
   "edges" [["START" "plan"] ["plan" "render"] ["render" "composite"] ["composite" "END"]]
   "reducers" {"panel_results" "merge-list"}})

;; GRAPH = _build_graph().compile(name="mangaka_generate_page") — omitted (langgraph runtime).
(def graph (build-graph))
