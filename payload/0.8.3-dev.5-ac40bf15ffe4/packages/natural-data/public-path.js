// Shared by the host, native client bundle and standalone result renderer.
// Public URLs belong to the browser deployment; engine transport stays private.
export function normalizeBasePath(value = '') {
  if (typeof value !== 'string') throw new Error('publicBasePath 必须是路径字符串。');
  if (value.startsWith('//')) throw new Error('publicBasePath 不能使用跨域或重复斜杠路径。');
  const path = value.replace(/\/+$/, '');
  if (!path) return '';
  if (!path.startsWith('/') || path.startsWith('//') || /[\\?#\s\u0000-\u001f]/.test(path)) {
    throw new Error('publicBasePath 必须是同域路径，例如 /sqlbot；不能填写域名、查询参数或反斜杠。');
  }
  for (const segment of path.slice(1).split('/')) {
    let decoded;
    try { decoded = decodeURIComponent(segment); } catch { throw new Error('publicBasePath 编码无效。'); }
    if (!decoded || decoded === '.' || decoded === '..' || /[\/\\?#\u0000-\u001f]/.test(decoded)) {
      throw new Error('publicBasePath 包含无效路径段。');
    }
  }
  return new URL(path, 'http://localhost').pathname;
}

export function createPublicUrls(scope = globalThis) {
  const page = new URL(scope.location?.href || (scope.location?.origin || 'http://localhost') + '/');
  const doc = scope.document;
  const sameOrigin = value => {
    try { const url = new URL(value, page); return url.origin === page.origin && !url.username && !url.password ? url : null; }
    catch { return null; }
  };
  function discover() {
    if (scope.__NATURAL_DATA_BASE_PATH__ !== undefined) return normalizeBasePath(scope.__NATURAL_DATA_BASE_PATH__);
    const meta = doc?.querySelector?.('meta[name="natural-data-base-path"]');
    if (meta) return normalizeBasePath(meta.content);
    // Result pages have their own stable route, including when opened directly.
    const viewer = page.pathname.lastIndexOf('/natural-data/');
    if (viewer >= 0) return normalizeBasePath(page.pathname.slice(0, viewer));
    const scriptBase = src => {
      const url = sameOrigin(src);
      const marker = url?.pathname.match(/\/plugins(?:\/|$)/);
      return marker ? normalizeBasePath(url.pathname.slice(0, marker.index)) : null;
    };
    // Capture this while the plugin script executes, before its factory runs.
    if (doc?.currentScript?.src) {
      const base = scriptBase(doc.currentScript.src);
      if (base !== null) return base;
    }
    const baseTag = doc?.querySelector?.('base[href]');
    if (baseTag) {
      const base = sameOrigin(baseTag.href);
      if (base) return normalizeBasePath(new URL('.', base).pathname);
    }
    for (const script of Array.from(doc?.scripts || []).reverse()) {
      if (!script.src) continue;
      const base = scriptBase(script.src);
      if (base !== null) return base;
    }
    // Bare application entry URLs (/app and /app/) also work without metadata.
    const path = /\.[^/]+$/.test(page.pathname) ? new URL('.', page).pathname : page.pathname;
    return normalizeBasePath(path);
  }
  const basePath = discover();
  const route = path => {
    if (!/^\/natural-data(?:\/|\?|$)/.test(path)) throw new Error('无效的问数资源路径。');
    return basePath + path;
  };
  // Rebase historical URLs after moving the same installation to another path.
  // Only the two owned result routes are accepted; foreign origins stay denied.
  const result = (value, kind = 'view') => {
    if (typeof value !== 'string' || !value) return null;
    const url = sameOrigin(value);
    const path = kind === 'export' ? '/natural-data/api/export' : '/natural-data/view';
    if (!url || !url.pathname.endsWith(path)) return null;
    return route(path + url.search + url.hash);
  };
  return {basePath, route, result};
}
