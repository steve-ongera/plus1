"""JWT authentication for WebSockets.

Browsers can't send an Authorization header when opening a WebSocket, so the
frontend passes the access token in the query string:

    ws://localhost:8000/ws/notifications/?token=<access_token>

Tokens are short-lived (30 min). Avoid logging full WebSocket URLs.
"""
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ObjectDoesNotExist
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import AccessToken


@database_sync_to_async
def _get_user(raw_token):
    try:
        token = AccessToken(raw_token)
        return get_user_model().objects.get(pk=token[api_settings.USER_ID_CLAIM], is_active=True)
    except (TokenError, ObjectDoesNotExist, KeyError, ValueError, TypeError):
        return AnonymousUser()


class JWTAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        scope = dict(scope)
        params = parse_qs(scope.get("query_string", b"").decode())
        token = (params.get("token") or [None])[0]
        scope["user"] = await _get_user(token) if token else AnonymousUser()
        return await super().__call__(scope, receive, send)
