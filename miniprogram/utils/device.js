function randomText() {
  return Math.random().toString(36).slice(2, 10);
}

function getDeviceId() {
  let deviceId = wx.getStorageSync("lobster_device_id") || "";
  if (!deviceId) {
    deviceId = `mp_${Date.now().toString(36)}_${randomText()}_${randomText()}`;
    wx.setStorageSync("lobster_device_id", deviceId);
  }
  return deviceId;
}

module.exports = {
  getDeviceId
};

