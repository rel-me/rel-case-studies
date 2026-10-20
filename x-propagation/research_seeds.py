"""One-off bounded seed review through the supported REL adapter only."""
import asyncio
import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path
from rel_playwright.async_api import async_playwright
from selectolax.parser import HTMLParser
from collector import parse_posts, text

ROOT = Path(__file__).resolve().parent
POSTS = [
    ('EricLDaugh', '2109330545834442967'),
    ('bennyjohnson', '2108598782610829649'),
    ('spencerpratt', '2108553958696399249'),
    ('LauraLoomer', '2109352779760218212'),
    ('RichardGrenell', '2109265270984347933'),
    ('GuntherEagleman', '2109730359202177260'),
    ('elonmusk', '2109346652565946776'),
]
PROFILES = ['MarkMeadows', 'JackPosobiec', 'catturd2', 'DC_Draino', 'EndWokeness',
            'libsoftiktok', 'johnrich', 'ScottPresler', 'DineshDSouza', 'w_terrence',
            'BrandonStraka', 'DonaldJTrumpJr']


async def main():
    folder = ROOT / 'captures' / 'seed-expansion'
    folder.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(session_id='Session16781')
        page = await browser.new_page()
        requests = [(handle, f'https://x.com/{handle}/status/{post}', post) for handle, post in POSTS]
        requests += [(handle, f'https://x.com/{handle}', 'profile') for handle in PROFILES]
        for handle, url, key in requests:
            path = folder / f'{handle}-{key}.html'
            if path.with_suffix('.json').exists():
                continue
            result = {'url': url, 'session_id': 'Session16781'}
            try:
                response = await page.goto(url, timeout=45000)
                html = await page.content()
                ready = HTMLParser(html).css_first('[data-testid="UserName"]') is not None if key == 'profile' else any(p['post_id'] == key for p in parse_posts(html, page.url))
                if not ready:
                    await page.wait_for_selector('[data-testid="UserName"]' if key == 'profile' else 'article[data-testid="tweet"]', timeout=15000)
                    html = await page.content()
                path.write_text(html)
                result.update(collected_at_utc=datetime.now(timezone.utc).isoformat(), final_url=page.url,
                              capture=str(path.relative_to(ROOT)), http_status=response.status)
                tree = HTMLParser(html)
                if key == 'profile':
                    result.update(name=text(tree.css_first('[data-testid="UserName"]')),
                                  bio=text(tree.css_first('[data-testid="UserDescription"]')),
                                  recent_visible_posts=[{'url': p['url'], 'text': p['text']} for p in parse_posts(html, page.url)[:3]])
                else:
                    result['posts'] = parse_posts(html, page.url)
                    result['target_verified'] = any(p['post_id'] == key for p in result['posts'])
            except Exception as exc:
                result['error'] = str(exc)
                try:
                    path.write_text(await page.content())
                    result['capture'] = str(path.relative_to(ROOT))
                except Exception as capture_error:
                    result['capture_error'] = str(capture_error)
            path.with_suffix('.json').write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n')
            if key == 'profile':
                print(json.dumps({k:result.get(k) for k in ['url','name','bio','error']},ensure_ascii=False),flush=True)
            else:
                print(json.dumps({'url':url,'posts':[{'url':p['url'],'text':p['text']} for p in result.get('posts',[]) if p['post_id']==key],'error':result.get('error')},ensure_ascii=False),flush=True)
        await browser.close()  # The adapter preserves a caller-owned session.


if __name__ == '__main__':
    with (ROOT / 'data' / 'collector.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(main())
