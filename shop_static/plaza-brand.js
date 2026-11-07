/* 选品广场品牌处理：
 * - 默认（不带参数 / brand=bihuo）：不显示任何品牌，标题只写「选品广场」
 * - OEM（?brand=<mark>&brand_name=<显示名>）：换成 OEM 品牌名，绝不出现打包默认品牌 */
(function () {
  'use strict';
  function readParam(key) {
    try { return String(new URLSearchParams(location.search || '').get(key) || '').trim(); } catch (e) { return ''; }
  }
  var mark = readParam('brand').toLowerCase();
  var name = readParam('brand_name');
  var stored = null;
  try { stored = JSON.parse(sessionStorage.getItem('plaza_brand') || 'null'); } catch (e) { stored = null; }
  if (!mark && stored && stored.mark) { mark = String(stored.mark); name = String(stored.name || ''); }
  if (mark === 'bihuo') { mark = ''; name = ''; }
  var isOem = !!mark;
  var NAMES = { daka: '\u5927\u5496AI\u5458\u5de5' };
  if (isOem) {
    name = name || NAMES[mark] || 'AI\u5458\u5de5';
    try { sessionStorage.setItem('plaza_brand', JSON.stringify({ mark: mark, name: name })); } catch (e) {}
  } else {
    try { sessionStorage.removeItem('plaza_brand'); } catch (e) {}
  }

  var isProduct = /p_preview\.html/.test(location.pathname) || document.title.indexOf('\u5546\u54c1\u8be6\u60c5') >= 0;

  function withBrand(href) {
    if (!isOem || !href || !/^(?:\.\/)?(?:plaza|p_preview)\.html(?:[?#]|$)/.test(href)) return href;
    if (/(?:^|[?&])brand=/.test(href)) return href;
    var sep = href.indexOf('?') >= 0 ? '&' : '?';
    return href + sep + 'brand=' + encodeURIComponent(mark) + '&brand_name=' + encodeURIComponent(name);
  }

  function apply() {
    var suffix = isOem ? name : '';
    document.title = isProduct
      ? ('\u5546\u54c1\u8be6\u60c5 \u00b7 ' + (suffix ? suffix + '\u9009\u54c1\u5e7f\u573a' : '\u9009\u54c1\u5e7f\u573a'))
      : ('\u9009\u54c1\u5e7f\u573a' + (suffix ? ' \u00b7 ' + suffix : ''));

    var brandSpan = document.querySelector('.plaza-brand > span');
    if (brandSpan) {
      brandSpan.textContent = suffix;
      brandSpan.style.display = suffix ? '' : 'none';
    }
    var sep = document.querySelector('.plaza-brand-sep');
    if (sep) sep.style.display = suffix ? '' : 'none';
    var logo = document.querySelector('.plaza-brand svg');
    if (logo) logo.style.display = 'none';

    var footer = document.querySelector('.plaza-footer-inner > div');
    if (footer) footer.textContent = '\u00a9 2026 ' + (suffix ? suffix + ' \u00b7 ' : '\u00b7 ') + '\u9009\u54c1\u5e7f\u573a';

    var home = document.querySelector('.plaza-brand');
    if (home) home.setAttribute('aria-label', (suffix ? suffix + ' \u00b7 ' : '') + '\u9009\u54c1\u5e7f\u573a\u9996\u9875');

    var links = document.querySelectorAll('a[href]');
    for (var i = 0; i < links.length; i += 1) {
      var href = links[i].getAttribute('href') || '';
      var next = withBrand(href);
      if (next !== href) links[i].setAttribute('href', next);
    }
  }

  document.addEventListener('click', function (evt) {
    if (!isOem) return;
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
