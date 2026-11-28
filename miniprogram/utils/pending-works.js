const KEY = "lobster_pending_works";
const TTL_MS = 30 * 60 * 1000;

function list() {
  try {
    const rows = wx.getStorageSync(KEY) || [];
    return Array.isArray(rows) ? rows : [];
  } catch (e) {
    return [];
  }
}

function save(rows) {
  try { wx.setStorageSync(KEY, (rows || []).slice(0, 50)); } catch (e) {}
}

function add(row) {
  if (!row || !row.id) return;
  const now = Date.now();
  const rows = list().filter((x) => x && x.id !== row.id);
  rows.unshift(Object.assign({ created_at: now, time: fmt(now) }, row));
  save(rows);
}

function remove(id) {
  save(list().filter((x) => x && x.id !== id));
}

function prune() {
  const now = Date.now();
  const rows = list().filter((x) => x && now - Number(x.created_at || 0) < TTL_MS);
  save(rows);
  return rows;
}

function fmt(ts) {
  const d = new Date(Number(ts) || Date.now());
  const p = (n) => (n < 10 ? "0" + n : "" + n);
  return (d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
}

module.exports = { list, add, remove, prune };