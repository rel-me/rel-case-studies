import {createApi} from './api.js';
const BASE = '/case-studies/prop13-santa-cruz';
const ALLOWED = new Set(['/index.html', '/style.css', '/app.js', '/social.svg', '/rel-logo.png', '/vendor/leaflet.js', '/vendor/leaflet.css', '/vendor/leaflet.markercluster.js', '/vendor/MarkerCluster.css', '/vendor/leaflet-LICENSE.txt', '/vendor/markercluster-LICENSE.txt']);
const API_PATHS=new Set(['/api/summary','/api/records','/api/record','/api/map']);
function json(value,status=200,extra={}){
  return Response.json(value,{status,headers:{'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Cross-Origin-Resource-Policy':'same-origin','X-Robots-Tag':'noindex, nofollow, noarchive',...extra}});
}
export function createWorker(snapshot) {
  const api=createApi(snapshot);
  return {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== BASE && !url.pathname.startsWith(BASE + '/')) return new Response('Not found', { status: 404 });
    if (!['GET', 'HEAD'].includes(request.method)) return new Response('Method not allowed', { status: 405, headers: { Allow: 'GET, HEAD' } });
    let asset = url.pathname.slice(BASE.length);
    if(API_PATHS.has(asset)){
      if(!env.STUDY_RATE_LIMITER)return json({error:'Temporarily unavailable'},503);
      const {success}=await env.STUDY_RATE_LIMITER.limit({key:request.headers.get('CF-Connecting-IP')||'local-preview'});
      if(!success)return json({error:'Too many requests. Please wait a minute.'},429,{'Retry-After':'60'});
      if(url.search.length>2000)return json({error:'Query too long'},400);
      try{
        const value=api(asset,url.searchParams);
        if(value==null)return json({error:'Record not found'},404);
        return request.method==='HEAD'?new Response(null,{headers:{'Cache-Control':'private, no-store'}}):json(value);
      }catch{return json({error:'Invalid request parameters'},400);}
    }
    if (!asset || asset === '/') asset = '/index.html';
    if (!ALLOWED.has(asset)) return new Response('Not found', { status: 404, headers:{'Cache-Control':'no-store'} });
    url.pathname = asset;url.search = '';
    const upstream = await env.ASSETS.fetch(new Request(url, request));
    const response = new Response(upstream.body, upstream);
    response.headers.set('X-Content-Type-Options', 'nosniff');
    response.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');
    response.headers.set('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://tile.openstreetmap.org; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
    response.headers.set('Cache-Control', asset==='/index.html'?'no-cache':'public, max-age=300');
    return response;
  }
};
}
