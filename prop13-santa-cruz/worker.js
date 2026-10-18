const BASE = '/case-studies/prop13-santa-cruz';
const ALLOWED = new Set(['/index.html', '/style.css', '/app.js', '/data.json', '/data.csv', '/social.svg']);

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== BASE && !url.pathname.startsWith(BASE + '/')) {
      return new Response('Not found', { status: 404 });
    }
    if (!['GET', 'HEAD'].includes(request.method)) {
      return new Response('Method not allowed', { status: 405, headers: { Allow: 'GET, HEAD' } });
    }
    let asset = url.pathname.slice(BASE.length);
    if (!asset || asset === '/') asset = '/index.html';
    if (!ALLOWED.has(asset)) return new Response('Not found', { status: 404 });
    url.pathname = asset;
    url.search = '';
    const upstream = await env.ASSETS.fetch(new Request(url, request));
    const response = new Response(upstream.body, upstream);
    response.headers.set('X-Content-Type-Options', 'nosniff');
    response.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');
    response.headers.set('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
    response.headers.set('Cache-Control', 'public, max-age=300');
    if (asset === '/data.csv') response.headers.set('Content-Disposition', 'attachment; filename="santa-cruz-property-tax-study.csv"');
    return response;
  }
};
