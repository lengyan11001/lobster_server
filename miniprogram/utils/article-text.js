function decodeEntities(text) {
  return String(text || "")
    .replace(/&nbsp;/gi, " ")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/&amp;/gi, "&");
}

function stripHtml(text) {
  return String(text || "")
    .replace(/<style[\s\S]*?<\/style>/gi, "")
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/(p|div|section|article|h[1-6]|li|tr|blockquote|pre)>/gi, "\n\n")
    .replace(/<[^>]+>/g, "");
}

function pushImage(list, value) {
  const url = String(value || "").trim();
  if (!/^https?:\/\//i.test(url)) return;
  if (list.indexOf(url) < 0) list.push(url);
}

function extractImageUrls(text, existing) {
  const raw = String(text || "");
  const urls = [];
  (existing || []).forEach((url) => pushImage(urls, url));
  let match;
  const markdown = /!\[[^\]]*\]\(\s*<?(https?:\/\/[^\s)>]+)>?\s*\)/gi;
  while ((match = markdown.exec(raw))) pushImage(urls, match[1]);
  const html = /<img\b[^>]*\bsrc\s*=\s*(['"])(https?:\/\/.*?)\1[^>]*>/gi;
  while ((match = html.exec(raw))) pushImage(urls, match[2]);
  const bare = /(https?:\/\/[^\s<>"')]+\.(?:png|jpe?g|webp|gif)(?:\?[^\s<>"')]*)?)/gi;
  while ((match = bare.exec(raw))) pushImage(urls, match[1]);
  return urls;
}

function stripInlineImages(text) {
  return String(text || "")
    .replace(/!\[[^\]]*\]\(\s*<?https?:\/\/[^\s)>]+>?\s*\)/gi, "")
    .replace(/<img\b[^>]*>/gi, "")
    .replace(/https?:\/\/[^\s<>"')]+\.(?:png|jpe?g|webp|gif)(?:\?[^\s<>"')]*)?/gi, "");
}

function cleanSuggestionSections(text) {
  const lines = String(text || "").split("\n");
  const out = [];
  let skipping = false;
  const headerRe = /^(配图建议|封面建议|排版建议|图片建议|建议配图|头图建议|插图建议|配图说明|图片素材|标签建议|标题建议)\s*[：:]?/;
  const labeledRe = /^(配图|封面图?|排版)\s*[：:]/;
  for (const raw of lines) {
    const line = String(raw || "");
    const trimmed = line.trim();
    if (headerRe.test(trimmed) || labeledRe.test(trimmed)) {
      skipping = true;
      continue;
    }
    if (skipping) {
      if (!trimmed) {
        skipping = false;
        continue;
      }
      if (/^(\d+[.、)]|[-*•])\s*/.test(trimmed)) continue;
      if (trimmed.length <= 80) continue;
      skipping = false;
    }
    if (/^以下是.*(文章|正文|内容)/.test(trimmed)) continue;
    out.push(line);
  }
  return out.join("\n");
}

function cleanArticleText(text) {
  let value = stripInlineImages(text);
  value = stripHtml(value);
  value = decodeEntities(value);
  value = stripHtml(value);
  value = value.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  value = cleanSuggestionSections(value);
  value = value
    .replace(/^\s*#{1,6}\s*/gm, "")
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^\s*[-*_]\s*$/gm, "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return value;
}

function parseArticleContent(text, existingImages) {
  const images = extractImageUrls(text, existingImages);
  return { text: cleanArticleText(text), images };
}

module.exports = { cleanArticleText, parseArticleContent, extractImageUrls };
