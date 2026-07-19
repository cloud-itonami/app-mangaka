/**
 * Convert rendered SVG pages to PNG via sharp for easy viewing.
 */
import * as fs from "node:fs";
import sharp from "sharp";

const REPO = "/Users/junkawasaki/github/ghosthacker/260123-jump";
const SVG_DIR = `${REPO}/resources/episodes/arc0-1-origin/rendered-pages`;
const PNG_DIR = `${REPO}/resources/episodes/arc0-1-origin/rendered-pages-png`;

async function main() {
  fs.mkdirSync(PNG_DIR, { recursive: true });
  const svgs = fs.readdirSync(SVG_DIR).filter((f) => f.endsWith(".svg"));
  for (const f of svgs) {
    const svgPath = `${SVG_DIR}/${f}`;
    const pngPath = `${PNG_DIR}/${f.replace(/\.svg$/, ".png")}`;
    try {
      await sharp(svgPath, { density: 150 }).png().toFile(pngPath);
      console.log(`${f} → ${pngPath.split("/").pop()}`);
    } catch (e) {
      console.log(`${f}: FAIL ${e instanceof Error ? e.message.slice(0, 100) : String(e)}`);
    }
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
