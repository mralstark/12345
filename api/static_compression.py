"""Compress public interface assets without compressing private API responses."""
from starlette.middleware.gzip import GZipMiddleware


class PublicAssetCompression:
    def __init__(self, app):
        self.app = app
        self.compressed = GZipMiddleware(app, minimum_size=1024, compresslevel=6)

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        public = path == '/' or (path.startswith('/static/') and path.endswith(('.js', '.css', '.svg')))
        if scope['type'] == 'http' and scope.get('method') in {'GET', 'HEAD'} and public:
            await self.compressed(scope, receive, send)
        else:
            await self.app(scope, receive, send)
