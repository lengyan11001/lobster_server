import fs from "node:fs/promises";
import path from "node:path";

const args = process.argv.slice(2);
const option = (name, fallback = "") => {
  const index = args.indexOf(name);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
};

const root = path.resolve(option("--root", process.cwd()));
const output = path.resolve(root, option("--output", "static/js/i18n-generated.js"));
const curated = path.resolve(root, option("--curated", "static/js/i18n.js"));
const sourceArgs = args.flatMap((value, index) => value === "--source" && args[index + 1] ? [args[index + 1]] : []);
const sources = sourceArgs.length ? sourceArgs : ["static/index.html", "static/views", "static/js"];
const dryRun = args.includes("--dry-run");
const extensions = new Set([".html", ".js"]);
const ignoredNames = new Set([
  "i18n.js",
  "h5-i18n.js",
  "i18n-generated.js",
  "h5-i18n-generated.js",
  "chat_副本.js",
]);

async function walk(target) {
  const stat = await fs.stat(target);
  if (stat.isFile()) return [target];
  const entries = await fs.readdir(target, { withFileTypes: true });
  const nested = await Promise.all(entries.map(async (entry) => {
    if (entry.name === "libs" || entry.name === "geojson" || entry.name === "node_modules") return [];
    return walk(path.join(target, entry.name));
  }));
  return nested.flat();
}

function decodeNumericEntities(value) {
  return value.replace(/&#(x?[0-9a-f]+);/gi, (_, raw) => {
    const radix = raw[0].toLowerCase() === "x" ? 16 : 10;
    const number = parseInt(radix === 16 ? raw.slice(1) : raw, radix);
    return Number.isFinite(number) ? String.fromCodePoint(number) : _;
  });
}

function cleanSegment(value) {
  return value
    .replace(/\s+/g, " ")
    .replace(/^[\s，。！？：；、,.!?;:+\-/%&]+|[\s，：；、,:;+\-/%&]+$/g, "")
    .trim();
}

function extractSegments(source) {
  const decoded = decodeNumericEntities(source);
  const matches = decoded.match(/[\p{Script=Han}][\p{Script=Han}A-Za-z0-9０-９ \t，。！？：；、（）《》【】“”‘’·+\-/%&,.!?]{0,159}/gu) || [];
  return matches
    .map(cleanSegment)
    .filter((value) => {
      const han = value.match(/[\p{Script=Han}]/gu) || [];
      if (han.length < 2 || value.length > 160) return false;
      if (/^(第|共)?\d+$/.test(value)) return false;
      return true;
    });
}

function existingKeys(source) {
  const keys = new Set();
  const keyPattern = /["']([^"'\r\n]*[\p{Script=Han}][^"'\r\n]*)["']\s*:/gu;
  let match;
  while ((match = keyPattern.exec(source))) keys.add(match[1]);
  return keys;
}

const BATCH_SEPARATOR = "[[[LOBSTER_I18N_BREAK]]]";

async function translateBatch(texts) {
  const joined = texts.join(`\n${BATCH_SEPARATOR}\n`);
  const query = new URLSearchParams({ client: "gtx", sl: "zh-CN", tl: "en", dt: "t", q: joined });
  const response = await fetch(`https://translate.googleapis.com/translate_a/single?${query}`, {
    headers: { "User-Agent": "lobster-ui-i18n-builder/1.0" },
  });
  if (!response.ok) throw new Error(`translation failed (${response.status})`);
  const data = await response.json();
  const translated = Array.isArray(data?.[0]) ? data[0].map((part) => part?.[0] || "").join("") : "";
  const parts = translated.split(BATCH_SEPARATOR).map((value) => value.trim());
  if (parts.length !== texts.length || parts.some((value) => !value)) {
    throw new Error(`translation batch boundary mismatch (${texts.length} requested, ${parts.length} returned)`);
  }
  return parts;
}

async function mapConcurrent(values, concurrency, mapper) {
  const result = new Array(values.length);
  let cursor = 0;
  async function worker() {
    while (cursor < values.length) {
      const index = cursor++;
      let lastError;
      for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
          result[index] = await mapper(values[index]);
          lastError = null;
          break;
        } catch (error) {
          lastError = error;
          await new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1)));
        }
      }
      if (lastError) throw lastError;
      if ((index + 1) % 100 === 0) console.log(`translated ${index + 1}/${values.length}`);
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, values.length || 1) }, worker));
  return result;
}

const files = (await Promise.all(sources.map((source) => walk(path.resolve(root, source))))).flat()
  .filter((file) => extensions.has(path.extname(file)) && !ignoredNames.has(path.basename(file)));
const curatedSource = await fs.readFile(curated, "utf8");
const curatedKeys = existingKeys(curatedSource);
const segments = new Set();
for (const file of files) {
  const source = await fs.readFile(file, "utf8");
  extractSegments(source).forEach((value) => {
    if (!curatedKeys.has(value)) segments.add(value);
  });
}
const ordered = [...segments].sort((a, b) => a.localeCompare(b, "zh-CN"));
console.log(`${ordered.length} untranslated UI phrases found in ${files.length} source files`);
if (dryRun) process.exit(0);

const batches = [];
let batch = [];
let batchLength = 0;
for (const phrase of ordered) {
  if (batch.length >= 30 || (batch.length && batchLength + phrase.length > 2800)) {
    batches.push(batch);
    batch = [];
    batchLength = 0;
  }
  batch.push(phrase);
  batchLength += phrase.length + BATCH_SEPARATOR.length + 2;
}
if (batch.length) batches.push(batch);
console.log(`${batches.length} translation batches prepared`);
const translatedBatches = await mapConcurrent(batches, 8, translateBatch);
const translations = translatedBatches.flat();
const catalog = Object.fromEntries(ordered.map((key, index) => [key, translations[index]]));
const banner = "// Generated by scripts/generate_ui_i18n_catalog.mjs. Curated translations override this catalog.\n";
const content = `${banner}window.LobsterGeneratedEn = Object.freeze(${JSON.stringify(catalog, null, 2)});\n`;
await fs.mkdir(path.dirname(output), { recursive: true });
await fs.writeFile(output, content, "utf8");
console.log(`wrote ${ordered.length} translations to ${output}`);
