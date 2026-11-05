import httpx
import pytest

from api.static_compression import PublicAssetCompression


@pytest.mark.parametrize('path,compressed', [
    ('/', True), ('/static/app.js', True), ('/static/styles.css', True),
    ('/static/icon.svg', True), ('/api/me', False), ('/api/reports/file', False),
    ('/static/photo.jpg', False), ('/downloads/private.js', False),
])
async def test_only_public_interface_assets_are_compressed(path, compressed):
    body = b'public static content ' * 1000

    async def application(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200,
                    'headers': [(b'content-type', b'text/plain'), (b'content-length', str(len(body)).encode())]})
        await send({'type': 'http.response.body', 'body': body})

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=PublicAssetCompression(application)),
                                base_url='http://test') as client:
        response = await client.get(path, headers={'Accept-Encoding': 'gzip'})
        assert response.content == body
        assert (response.headers.get('Content-Encoding') == 'gzip') is compressed
        if compressed:
            assert int(response.headers['Content-Length']) < len(body) // 4
            assert 'Accept-Encoding' in response.headers['Vary']
        plain = await client.get(path, headers={'Accept-Encoding': 'identity'})
        assert plain.content == body and 'Content-Encoding' not in plain.headers


async def test_actual_frontend_assets_keep_security_headers(client):
    for path in ['/', '/static/app.js', '/static/styles.css']:
        response = await client.get(path, headers={'Accept-Encoding': 'gzip'})
        assert response.status_code == 200
        assert response.headers['Content-Encoding'] == 'gzip'
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
        assert 'Content-Security-Policy' in response.headers
