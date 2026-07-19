/**
 * PDF export — assembles rendered page PNGs into a single manga manuscript PDF.
 *
 * Features:
 * - A4 trim (210×297mm) per page, 420×297mm for spreads
 * - Bleed area (±3mm) preserved (PNG already includes bleed)
 * - Trim marks (corner crop marks) on each page for printer reference
 * - Spreads (p6-p7, p39-p40) rendered as single landscape pages
 *
 * Usage:
 *   npx tsx src/export-pdf.ts                              # lossless PNG embed (print master)
 *   npx tsx src/export-pdf.ts --jpeg                       # mozjpeg q=82 (preview / share, ~10-15x smaller)
 *   npx tsx src/export-pdf.ts --jpeg --jpeg-quality 90     # higher quality JPEG
 *   npx tsx src/export-pdf.ts --no-trim-marks
 *   npx tsx src/export-pdf.ts --output ../my.pdf
 */
import * as fs from "node:fs";
import { PDFDocument, rgb, PDFPage } from "pdf-lib";
import sharp from "sharp";

const REPO = "/Users/junkawasaki/github/ghosthacker/260123-jump";
const EPISODE_PATH = `${REPO}/resources/episodes/arc0-1-origin/episode.jsonld`;
const PNG_DIR = `${REPO}/resources/episodes/arc0-1-origin/rendered-pages-png`;
const SVG_DIR = `${REPO}/resources/episodes/arc0-1-origin/rendered-pages`;

const MM_TO_PT = 2.834645669; // 1mm = 2.83 pt (PDF unit)
const TRIM_W = 210, TRIM_H = 297, BLEED = 3;
const PAGE_W = (TRIM_W + 2*BLEED) * MM_TO_PT;
const PAGE_H = (TRIM_H + 2*BLEED) * MM_TO_PT;
const SPREAD_PAGE_W = (TRIM_W*2 + 2*BLEED) * MM_TO_PT;

interface CliArgs { noTrimMarks: boolean; output: string; jpeg: boolean; jpegQuality: number }
function parseArgs(): CliArgs {
  const a = process.argv.slice(2);
  const o: CliArgs = { noTrimMarks: false, output: `${REPO}/arc0-1-origin.pdf`, jpeg: false, jpegQuality: 82 };
  for (let i = 0; i < a.length; i++) {
    if (a[i] === "--no-trim-marks") o.noTrimMarks = true;
    else if (a[i] === "--output" && a[i+1]) o.output = a[++i];
    else if (a[i] === "--jpeg") o.jpeg = true;
    else if (a[i] === "--jpeg-quality" && a[i+1]) { o.jpeg = true; o.jpegQuality = Number(a[++i]); }
  }
  return o;
}

/** Draw 4 corner trim marks (Japanese printer convention: 5mm crop marks + 3mm bleed marks) */
function drawTrimMarks(page: PDFPage, isSpread: boolean) {
  const trimW = (isSpread ? TRIM_W * 2 : TRIM_W) * MM_TO_PT;
  const trimH = TRIM_H * MM_TO_PT;
  const bleedPt = BLEED * MM_TO_PT;
  const markLen = 5 * MM_TO_PT;
  const markGap = 2 * MM_TO_PT;
  const ink = rgb(0, 0, 0);
  const lineW = 0.3;

  // 4 corners — outer (trim line) marks
  const corners = [
    { x: bleedPt, y: bleedPt },                              // bottom-left
    { x: bleedPt + trimW, y: bleedPt },                      // bottom-right
    { x: bleedPt, y: bleedPt + trimH },                      // top-left
    { x: bleedPt + trimW, y: bleedPt + trimH },              // top-right
  ];
  for (const c of corners) {
    // Horizontal mark
    page.drawLine({
      start: { x: c.x - markLen - markGap, y: c.y },
      end:   { x: c.x - markGap, y: c.y },
      color: ink, thickness: lineW,
    });
    page.drawLine({
      start: { x: c.x + markGap, y: c.y },
      end:   { x: c.x + markLen + markGap, y: c.y },
      color: ink, thickness: lineW,
    });
    // Vertical mark
    page.drawLine({
      start: { x: c.x, y: c.y - markLen - markGap },
      end:   { x: c.x, y: c.y - markGap },
      color: ink, thickness: lineW,
    });
    page.drawLine({
      start: { x: c.x, y: c.y + markGap },
      end:   { x: c.x, y: c.y + markLen + markGap },
      color: ink, thickness: lineW,
    });
  }
}

async function main() {
  const cli = parseArgs();
  const ep = JSON.parse(fs.readFileSync(EPISODE_PATH, "utf-8"));

  const doc = await PDFDocument.create();
  doc.setTitle(ep["dct:title"] ?? "arc0-1");
  doc.setSubject("Ghost Hacker #00 - manga manuscript");
  doc.setCreator("ghosthacker lg-image-gen pipeline");

  const pages = ep["gh:pages"] ?? [];
  let added = 0;
  // Spread pages — skip second page (rendered as combined spread)
  const skippedPages = new Set<number>();
  for (const p of pages) {
    const layout = p["gh:pageLayoutV3"];
    if (layout?.["gh:pageType"] === "double-page-spread" && layout?.["gh:spreadWith"] > p["gh:pageNumber"]) {
      // First page of spread renders combined; skip the second
      skippedPages.add(layout["gh:spreadWith"]);
    }
  }

  for (const page of pages) {
    const pn = page["gh:pageNumber"];
    if (skippedPages.has(pn)) {
      console.log(`p${pn}: skipped (part of spread)`);
      continue;
    }
    const isSpread = page["gh:pageLayoutV3"]?.["gh:pageType"] === "double-page-spread";
    const pngPath = `${PNG_DIR}/page-${pn.toString().padStart(2, "0")}.png`;
    if (!fs.existsSync(pngPath)) { console.log(`p${pn}: PNG missing, skip`); continue; }

    const pngBuf = fs.readFileSync(pngPath);
    let img;
    if (cli.jpeg) {
      const jpegBuf = await sharp(pngBuf).jpeg({ quality: cli.jpegQuality, mozjpeg: true }).toBuffer();
      img = await doc.embedJpg(jpegBuf);
    } else {
      img = await doc.embedPng(pngBuf);
    }
    const w = isSpread ? SPREAD_PAGE_W : PAGE_W;
    const h = PAGE_H;
    const pdfPage = doc.addPage([w, h]);
    pdfPage.drawImage(img, { x: 0, y: 0, width: w, height: h });
    if (!cli.noTrimMarks) drawTrimMarks(pdfPage, isSpread);
    added++;
    console.log(`p${pn}: ${isSpread ? "spread" : "single"} (${(w/MM_TO_PT).toFixed(0)}×${(h/MM_TO_PT).toFixed(0)}mm)`);
  }

  const pdfBytes = await doc.save();
  fs.writeFileSync(cli.output, pdfBytes);
  const sizeMb = (pdfBytes.length / 1024 / 1024).toFixed(1);
  console.log(`\nDone: ${added} pages, ${sizeMb} MB → ${cli.output}`);
}

main().catch((e) => { console.error(e); process.exit(1); });
