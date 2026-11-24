"""ASGI entry point: plain HTTP goes to Django, WebSockets go to Channels."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Initialise Django first so models can be imported by the routing below.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from api.middleware import JWTAuthMiddleware  # noqa: E402
from api.routing import websocket_urlpatterns  # noqa: E402

# The WebSocket is authenticated with a JWT (not cookies), so cross-site
# WebSocket hijacking is not a concern and no origin validator is needed.
application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": JWTAuthMiddleware(URLRouter(websocket_urlpatterns)),
    }
)
