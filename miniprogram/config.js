const BRANDS = {
  bihuo: {
    mark: "bihuo",
    appName: "必火AI员工",
    primaryColor: "#10f5dd",
    logo: "/assets/bihu_128.png"
  },
  daka: {
    mark: "daka",
    appName: "大咖AI员工",
    primaryColor: "#00c8df",
    logo: "/assets/daka_128.png"
  }
};

function resolveBrandMark() {
  try {
    const ext = wx.getExtConfigSync ? (wx.getExtConfigSync() || {}) : {};
    const raw = String(ext.brand_mark || ext.brand || "bihuo").trim().toLowerCase();
    return BRANDS[raw] ? raw : "bihuo";
  } catch (err) {
    return "bihuo";
  }
}

const BRAND_MARK = resolveBrandMark();
const BRAND = BRANDS[BRAND_MARK];

function storageKey(key) {
  return `${String(key || "").replace(/:+$/, "")}:${BRAND_MARK}`;
}

module.exports = {
  API_BASE: "https://bhzn.top",
  APP_NAME: BRAND.appName,
  BRAND_MARK,
  BRAND,
  BRANDS,
  storageKey
};
