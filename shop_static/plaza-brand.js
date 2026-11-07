/* 选品广场 OEM 品牌：客户端带 ?brand=<mark>&brand_name=<显示名> 进来，
 * 广场页/商品详情页据此把必火品牌换掉；没带参数时保持必火原样。 */
(function () {
  'use strict';
  function readParam(name) {
    try { return String(new URLSearchParams(location.search || '').get(name) || '').trim(); } catch (e) { return ''; }
  }
  var mark = readParam('brand').toLowerCase();
  var name = readParam('brand_name');
  var stored = null;
  try { stored = JSON.parse(sessionStorage.getItem('plaza_brand') || 'null'); } catch (e) { stored = null; }
  if (!mark && stored && stored.mark) { mark = String(stored.mark); name = String(stored.name || ''); }
  if (!mark || mark === 'bihuo') return;
  var NAMES = { daka: '\u5927\u5496AI\u5458\u5de5' };
  name = name || NAMES[mark] || 'AI\u5458\u5de5';
  try { sessionStorage.setItem('plaza_brand', JSON.stringify({ mark: mark, name: name })); } catch (e) {}
  document.documentElement.setAttribute('data-brand', mark);

  function withBrand(href) {
    if (!href || !/^(?:\.\/)?(?:plaza|p_preview)\.html(?:[?#]|$)/.test(href)) return href;
    if (/(?:^|[?&])brand=/.test(href)) return href;
    var sep = href.indexOf('?') >= 0 ? '&' : '?';
    return href + sep + 'brand=' + encodeURIComponent(mark) + '&brand_name=' + encodeURIComponent(name);
  }

  function apply() {
    if (document.title && document.title.indexOf('\u5fc5\u706b') >= 0) {
      document.title = document.title.replace(/\u5fc5\u706b\u667a\u80fd/g, name).replace(/\u5fc5\u706b/g, name);
    }
    var brandSpan = document.querySelector('.plaza-brand > span');
    if (brandSpan) brandSpan.textContent = name;
    var logo = document.querySelector('.plaza-brand svg');
    if (logo) logo.style.display = 'none';
    var footer = document.querySelector('.plaza-footer-inner > div');
    if (footer) footer.textContent = '\u00a9 2026 ' + name + ' \u00b7 \u9009\u54c1\u5e7f\u573a';
    var home = document.querySelector('.plaza-brand');
    if (home) home.setAttribute('aria-label', name + ' \u00b7 \u9009\u54c1\u5e7f\u573a\u9996\u9875');
    var links = document.querySelectorAll('a[href]');
    for (var i = 0; i < links.length; i += 1) {
      var href = links[i].getAttribute('href') || '';
      var next = withBrand(href);
      if (next !== href) links[i].setAttribute('href', next);
    }
  }

  document.addEventListener('click', function (evt) {
    var node = evt.target;
    while (node && node !== document && !(node.tagName && node.tagName.toLowerCase() === 'a')) node = node.parentNode;
    if (!node || !node.getAttribute) return;
    var href = node.getAttribute('href') || '';
    var next = withBrand(href);
    if (next !== href) node.setAttribute('href', next);
  }, true);

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', apply);
  else apply();
  setTimeout(apply, 600);
  setTimeout(apply, 1800);
})();
