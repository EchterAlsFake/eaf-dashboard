"""Browser coverage uses intercepted synthetic reports, never production data."""
import json
from pathlib import Path

import pytest

playwright_api = pytest.importorskip('playwright.sync_api')


def test_analytics_view_filters_keeps_period_and_distinguishes_empty_error(tmp_path):
    root = Path(__file__).parents[1] / 'dashboard'
    report = dict(start='2026-10-12', end_exclusive='2026-11-09', updated_day='2026-11-11',
                  enabled=True, stale=False, minimum_count=20, rows=[
                      dict(week='2026-11-02', page='guide_xvideos', action='pageview', count=100),
                      dict(week='2026-11-02', page='guide_xvideos', action='open_download', count=25),
                  ])
    periods, failures = [], []
    with playwright_api.sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip('Playwright Chromium is not installed')
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.clock.install()
            def route_request(route):
                path = route.request.url.split('metrics.invalid', 1)[1]
                if path.startswith('/dashboard/api/analytics'):
                    periods.append(path)
                    route.fulfill(status=503 if failures else 200, content_type='application/json',
                                  body=json.dumps({'error': 'analytics_source_unavailable'} if failures else report))
                elif path.startswith('/dashboard/static/'):
                    file = root / 'static' / path.rsplit('/', 1)[1]
                    route.fulfill(path=file, content_type='text/css' if file.suffix == '.css' else 'application/javascript')
                else:
                    route.fulfill(content_type='text/html', body=(root / 'templates/dashboard.html').read_text())
            page.route('**/*', route_request)
            page.goto('https://metrics.invalid/dashboard/analytics')
            playwright_api.expect(page.locator('#content')).to_contain_text('Collection enabled')
            playwright_api.expect(page.locator('#analytics-search-table')).to_contain_text('25.0')
            page.locator('#analytics-search').fill('Open Download')
            playwright_api.expect(page.locator('#analytics-search-table tbody tr:visible')).to_have_count(1)
            page.locator('#analytics-days').select_option('7')
            playwright_api.expect(page.locator('#analytics-days')).to_have_value('7')
            page.clock.fast_forward(61000)
            playwright_api.expect(page.locator('#analytics-days')).to_have_value('7')
            assert periods[-1].endswith('days=7')
            page.screenshot(path=tmp_path / 'website-analytics.png', full_page=True)
            report.update(rows=[], stale=True, enabled=False)
            page.locator('#analytics-days').select_option('90')
            playwright_api.expect(page.locator('#content')).to_contain_text('No reportable activity for this period')
            playwright_api.expect(page.locator('#content')).to_contain_text('Collection disabled')
            playwright_api.expect(page.locator('#content')).to_contain_text('stale')
            failures.append(True)
            page.locator('#analytics-days').select_option('28')
            playwright_api.expect(page.locator('#content')).to_contain_text('analytics_source_unavailable')
        finally:
            browser.close()
