function parseServerTime(value) {
  if (value === null || value === undefined || value === "") return null;
  if (value instanceof Date) return isNaN(value.getTime()) ? null : value;
  if (typeof value === "number") {
    return new Date(value < 1000000000000 ? value * 1000 : value);
  }
  let text = String(value).trim();
  if (!text) return null;
  if (/^\d+$/.test(text)) {
    const n = Number(text);
    return new Date(n < 1000000000000 ? n * 1000 : n);
  }
  // 服务端多数 created_at 是“无时区的 UTC”，直接 new Date 会被当成本地时间，少 8 小时。
  if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(text) && !/(Z|[+-]\d{2}:?\d{2})$/.test(text)) {
    text = text.replace(" ", "T") + "Z";
  }
  const d = new Date(text);
  return isNaN(d.getTime()) ? null : d;
}

function pad(n) {
  return n < 10 ? "0" + n : "" + n;
}

function formatDateTime(value, withYear) {
  const d = parseServerTime(value);
  if (!d) return "";
  const base = `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return withYear ? `${d.getFullYear()}-${base}` : base;
}

module.exports = { parseServerTime, formatDateTime };
