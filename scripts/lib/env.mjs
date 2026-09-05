/**
 * Environment-driven launch options shared by the test config and the
 * standalone scripts.
 *
 * HTTP(S)_PROXY / NO_PROXY are honoured explicitly rather than left to
 * Chromium's env parsing, so the same settings apply on every platform and in
 * every launch mode.
 */

// Chromium's --proxy-bypass-list understands hostnames, .suffix patterns, IPs
// and CIDR blocks. NO_PROXY in the wild also carries entries it rejects (a bare
// "::", "*.foo" globs), and one bad rule discards the whole list. Normalise.
function normalizeBypass(noProxy) {
  const entries = noProxy
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean)
    .map((entry) => (entry.startsWith('*.') ? entry.slice(1) : entry))
    .filter((entry) => entry !== '*' && entry !== '::');

  return [...new Set(['localhost', '127.0.0.1', '[::1]', ...entries])].join(',');
}

/**
 * True for targets that must never be sent to an outbound proxy — a dev server
 * on loopback, or a service on a private network.
 *
 * Chromium's implicit loopback bypass does not survive an explicit
 * --proxy-server, and --proxy-bypass-list does not bring it back, so the proxy
 * has to be dropped at launch time instead.
 */
export function isLocalTarget(target) {
  if (!target) return false;

  let hostname;
  try {
    ({ hostname } = new URL(target));
  } catch {
    return false;
  }

  hostname = hostname.replace(/^\[|\]$/g, '');
  return (
    hostname === 'localhost' ||
    hostname.endsWith('.localhost') ||
    hostname === '::1' ||
    /^127\./.test(hostname) ||
    /^10\./.test(hostname) ||
    /^192\.168\./.test(hostname) ||
    /^172\.(1[6-9]|2\d|3[01])\./.test(hostname)
  );
}

/**
 * Proxy launch options from the environment, or undefined when no proxy is
 * configured or the target is local.
 *
 * @param {string} [target] URL the browser will be pointed at.
 * @returns {import('@playwright/test').LaunchOptions['proxy'] | undefined}
 */
export function proxyFromEnv(target) {
  if (isLocalTarget(target)) return undefined;

  const server =
    process.env.PLAYWRIGHT_PROXY ??
    process.env.HTTPS_PROXY ??
    process.env.https_proxy ??
    process.env.HTTP_PROXY ??
    process.env.http_proxy;

  if (!server) return undefined;

  return { server, bypass: normalizeBypass(process.env.NO_PROXY ?? process.env.no_proxy ?? '') };
}
