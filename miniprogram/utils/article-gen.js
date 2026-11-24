const app = getApp();
const hist = require("./gen-history");

const CHAT_MODEL = "deepseek-chat";

function pickContent(data) {
  const choice = data && data.choices && data.choices[0];
  const msg = choice && choice.message;
  const content = msg && msg.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((part) => (typeof part === "string" ? part : (part && part.text) || "")).join("");
  }
  return "";
}

function parseArticle(raw) {
  const text = String(raw || "").trim();
  const cleaned = text.replace(/^```(?:json)?/i, "").replace(/```$/, "").trim();
  try {
    const obj = JSON.parse(cleaned);
    return {
      title: String(obj.title || "").trim(),
      summary: String(obj.summary || obj.digest || "").trim(),
      body: String(obj.body || obj.content || "").trim()
    };
  } catch (err) {
    const lines = text.split(/\r?\n/).filter((line) => line.trim());
    return { title: (lines[0] || "").replace(/^#+\s*/, "").trim().slice(0, 60), summary: "", body: text };
  }
}

function extractUrls(data) {
  const urls = [];
  const push = (v) => {
    const t = String(v || "").trim();
    if (/^https?:/i.test(t) && urls.indexOf(t) < 0) urls.push(t);
  };
  const rows = (data && (data.data || data.items || data.images || data.results)) || [];
  if (Array.isArray(rows)) {
    rows.forEach((row) => {
      if (typeof row === "string") push(row);
      else if (row && typeof row === "object") push(row.url || row.image_url || row.source_url || (row.image_urls && row.image_urls[0]));
    });
  }
  push(data && (data.url || data.image_url));
  return urls;
}

function buildMessages(payload) {
  const topic = String(payload.topic || "").trim();
  const points = String(payload.points || "").trim();
  const audience = String(payload.audience || "").trim();
  const tone = String(payload.tone || "专业观点");
  const source = String(payload.sourceContent || "").trim();
  const system = source
    ? [
        "你是资深公众号主笔，擅长把一篇原文改写成可发布的新文章。",
        "只返回严格 JSON：{\"title\":\"标题\",\"summary\":\"80字以内摘要\",\"body\":\"正文\"}。",
        "复刻要求：保留原文的核心信息点、结构和观点，用完全不同的表达重写，不要照抄原句，不要大段近似。",
        "正文用中文分段，段落之间空行，不要 Markdown 符号，不要解释。标题不超过 30 字。"
      ].join("")
    : [
        "你是资深公众号主笔，擅长写可直接发布的微信文章。",
        "只返回严格 JSON：{\"title\":\"标题\",\"summary\":\"80字以内摘要\",\"body\":\"正文\"}。",
        "正文用中文分段，段落之间空行，不要 Markdown 符号，不要解释，不要出现「以下是」。",
        "标题不超过 30 字，要有信息量和点击欲；正文 900-1400 字。"
      ].join("");
  const user = [
    topic ? "主题：" + topic : "",
    points ? "要点与素材：" + points : "",
    audience ? "目标读者：" + audience : "",
    "写作风格：" + tone,
    source ? "参考原文（请改写，不要照抄）：\n" + source.slice(0, 6000) : "",
    "请直接输出 JSON。"
  ].filter(Boolean).join("\n");
  return { system, user };
}

function generateImages(histId, article) {
  const title = String(article.title || "公众号文章").slice(0, 60);
  const summary = String(article.summary || "").slice(0, 120);
  const prompts = [
    "公众号文章封面图，主题：" + title + "。简洁高级的商业摄影质感，构图干净，不要文字、不要 logo、不要水印。",
    "公众号文章配图，场景化表达：" + (summary || title) + "。真实自然、有生活感，适合公众号阅读，不要文字、不要 logo。",
    "公众号文章配图，细节特写/信息图风格，主题：" + title + "。现代、专业、有层次，不要文字、不要 logo。"
  ];
  const images = [];
  const tasks = prompts.map((prompt, index) => app.request({
    method: "POST",
    url: "/api/comfly-proxy/v1/images/generations",
    data: { model: "gpt-image-2", prompt, image_size: "3:2", aspect_ratio: "3:2", ratio: "3:2", num_images: 1, n: 1, response_format: "url" },
    timeout: 180000
  })
    .then((data) => {
      const url = extractUrls(data)[0];
      if (!url) throw new Error("没有返回图片");
      return app.request({
        method: "POST",
        url: "/api/assets/save-url",
        data: { url, media_type: "image", tags: "article,miniprogram", prompt, model: "gpt-image-2" },
        timeout: 120000
      }).catch(() => null).then(() => url);
    })
    .then((url) => { images[index] = url; })
    .catch(() => null));
  return Promise.all(tasks).then(() => {
    const list = images.filter(Boolean);
    hist.update(histId, { images: list, image_url: list[0] || "" });
  });
}

function runTask(histId, payload) {
  const { system, user } = buildMessages(payload || {});
  return app.request({
    method: "POST",
    url: "/api/sutui-chat/completions",
    data: { model: CHAT_MODEL, temperature: 0.7, messages: [{ role: "system", content: system }, { role: "user", content: user }] },
    timeout: 300000
  })
    .then((data) => {
      const raw = pickContent(data);
      if (!raw) throw new Error("模型没有返回内容");
      const article = parseArticle(raw);
      hist.update(histId, {
        title: article.title || String(payload.topic || "").slice(0, 24),
        summary: article.summary,
        content: article.body,
        status: "completed",
        status_text: "已完成"
      });
      return generateImages(histId, article).catch(() => null);
    })
    .catch((err) => {
      hist.update(histId, { status: "failed", status_text: "失败", error: String((err && err.message) || err).slice(0, 200) });
    });
}

module.exports = { runTask, buildMessages };
