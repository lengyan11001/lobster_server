const KEY = "gen_history_v1";

function loadAll() {
  try { const v = wx.getStorageSync(KEY); return Array.isArray(v) ? v : []; } catch (e) { return []; }
}

function persist(list) {
  try { wx.setStorageSync(KEY, list.slice(0, 200)); } catch (e) {}
}

function save(feature, entry) {
  const list = loadAll();
  const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  list.unshift(Object.assign({ id, feature, at: Date.now() }, entry));
  persist(list);
  return id;
}

function update(id, patch) {
  const list = loadAll();
  const idx = list.findIndex((x) => x.id === id);
  if (idx >= 0) { list[idx] = Object.assign({}, list[idx], patch); persist(list); }
}

function list(feature) {
  return loadAll().filter((x) => x.feature === feature);
}

function get(id) {
  return loadAll().find((x) => x.id === id) || null;
}

function remove(id) {
  persist(loadAll().filter((x) => x.id !== id));
}

module.exports = { save, update, list, get, remove };
