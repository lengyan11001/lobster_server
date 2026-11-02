// ATW(5B 50) / KA(4B 41) 私有 Opus 流 -> 标准 OGG Opus
// 与 APK ATWOpusConverter.java 保持同一套切帧逻辑：
//   1) 找分隔符位置；2) 取相邻间距的众数作为帧长 stride；3) 从第一个分隔符起按 stride 切（分隔符字节属于 Opus 包头，必须保留）

var _ct = (function() {
  var t = new Uint32Array(256)
  for (var i = 0; i < 256; i++) {
    var c = i << 24
    for (var j = 0; j < 8; j++) c = (c & 0x80000000) ? ((c << 1) ^ 0x04c11db7) : (c << 1), c = c >>> 0
    t[i] = c
  }
  return t
})()

function _crc(d) {
  var c = 0
  for (var i = 0; i < d.length; i++) c = ((c << 8) ^ _ct[((c >>> 24) ^ d[i]) & 0xFF]) >>> 0
  return c
}

function _le16(b, o, v) { b[o] = v & 0xFF; b[o + 1] = (v >>> 8) & 0xFF }
function _le32(b, o, v) { b[o] = v & 0xFF; b[o + 1] = (v >>> 8) & 0xFF; b[o + 2] = (v >>> 16) & 0xFF; b[o + 3] = (v >>> 24) & 0xFF }
function _le64(b, o, v) { _le32(b, o, v & 0xFFFFFFFF); _le32(b, o + 4, Math.floor(v / 0x100000000) & 0xFFFFFFFF) }

function _opusHead() {
  var b = new Uint8Array(19)
  var tag = 'OpusHead'
  for (var i = 0; i < 8; i++) b[i] = tag.charCodeAt(i)
  b[8] = 1      // version
  b[9] = 1      // channels
  _le16(b, 10, 312)   // pre-skip
  _le32(b, 12, 48000) // input sample rate
  _le16(b, 16, 0)     // output gain
  b[18] = 0           // mapping family
  return b
}

function _opusTags() {
  var vendor = 'AIREC'
  var b = new Uint8Array(8 + 4 + vendor.length + 4)
  var tag = 'OpusTags'
  for (var i = 0; i < 8; i++) b[i] = tag.charCodeAt(i)
  _le32(b, 8, vendor.length)
  for (var j = 0; j < vendor.length; j++) b[12 + j] = vendor.charCodeAt(j)
  _le32(b, 12 + vendor.length, 0)
  return b
}

function _page(pkts, sn, gp, seq, bos, eos) {
  var seg = [], pd = []
  for (var i = 0; i < pkts.length; i++) {
    var p = pkts[i], r = p.length, o = 0
    while (r >= 255) { seg.push(255); pd.push(p.slice(o, o + 255)); o += 255; r -= 255 }
    seg.push(r); pd.push(p.slice(o, o + r))
  }
  var hs = 27 + seg.length, ds = 0
  for (var j = 0; j < pd.length; j++) ds += pd[j].length
  var pg = new Uint8Array(hs + ds)
  pg[0] = 0x4F; pg[1] = 0x67; pg[2] = 0x67; pg[3] = 0x53   // OggS
  pg[4] = 0
  pg[5] = (bos ? 2 : 0) | (eos ? 4 : 0)
  _le64(pg, 6, gp); _le32(pg, 14, sn); _le32(pg, 18, seq); _le32(pg, 22, 0)
  pg[26] = seg.length
  for (var m = 0; m < seg.length; m++) pg[27 + m] = seg[m]
  var po = hs
  for (var n = 0; n < pd.length; n++) { pg.set(pd[n], po); po += pd[n].length }
  var crc = _crc(pg)
  pg[22] = crc & 0xFF; pg[23] = (crc >>> 8) & 0xFF; pg[24] = (crc >>> 16) & 0xFF; pg[25] = (crc >>> 24) & 0xFF
  return pg
}

function _extractFrames(raw, sep0, sep1) {
  var positions = []
  for (var i = 0; i < raw.length - 1; i++) {
    if (raw[i] === sep0 && raw[i + 1] === sep1) positions.push(i)
  }
  if (positions.length < 2) return []
  var gaps = {}
  for (var j = 1; j < positions.length; j++) {
    var g = positions[j] - positions[j - 1]
    gaps[g] = (gaps[g] || 0) + 1
  }
  var stride = 0, max = 0
  for (var k in gaps) {
    if (gaps[k] > max) { max = gaps[k]; stride = Number(k) }
  }
  var dataSize = stride - 2
  if (dataSize < 10 || dataSize > 500) return []
  var frames = [], pos = positions[0]
  while (pos + stride <= raw.length) {
    frames.push(raw.slice(pos, pos + stride))
    pos += stride
  }
  return frames
}

function convert(srcPath) {
  var fs = wx.getFileSystemManager()
  var outPath = srcPath + '.opus'
  try {
    var raw = new Uint8Array(fs.readFileSync(srcPath))
    var sep0 = 0, sep1 = 0
    if (raw[0] === 0x5B && raw[1] === 0x50) { sep0 = 0x5B; sep1 = 0x50 }
    else if (raw[0] === 0x4B && raw[1] === 0x41) { sep0 = 0x4B; sep1 = 0x41 }
    else return null
    var frames = _extractFrames(raw, sep0, sep1)
    if (!frames.length) return null
    var parts = [], sn = 0x41495243, seq = 0
    parts.push(_page([_opusHead()], sn, 0, seq++, true, false))
    parts.push(_page([_opusTags()], sn, 0, seq++, false, false))
    var gp = 312, i = 0
    while (i < frames.length) {
      var n = Math.min(50, frames.length - i)
      gp += n * 960
      var last = (i + n >= frames.length)
      parts.push(_page(frames.slice(i, i + n), sn, gp, seq++, false, last))
      i += n
    }
    var len = 0
    for (var a = 0; a < parts.length; a++) len += parts[a].length
    var out = new Uint8Array(len), off = 0
    for (var b = 0; b < parts.length; b++) { out.set(parts[b], off); off += parts[b].length }
    fs.writeFileSync(outPath, out.buffer, 'binary')
    return outPath
  } catch (e) { return null }
}

module.exports = { convert: convert }
