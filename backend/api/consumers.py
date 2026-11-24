"""WebSocket consumer: pushes notifications and live chat messages to a user.

Every signed-in socket joins the group `user_<id>`. services.push_to_user()
sends events to that group; the handlers below forward them to the browser.

Messages sent to the browser always look like: {"event": "...", "data": {...}}
"""
from channels.generic.websocket import AsyncJsonWebsocketConsumer


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if user is None or user.is_anonymous:
            await self.close(code=4401)  # not authenticated
            return

        self.group_name = f"user_{user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json({"event": "connected", "data": {"user": user.slug}})

    async def disconnect(self, code):
        group = getattr(self, "group_name", None)
        if group:
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        # Keep-alive from the browser: {"type": "ping"}
        if isinstance(content, dict) and content.get("type") == "ping":
            await self.send_json({"event": "pong"})

    # --- group event handlers (event["type"] with dots -> underscores) -------
    async def notification_message(self, event):
        await self.send_json({"event": "notification", "data": event["payload"]})

    async def chat_message(self, event):
        await self.send_json({"event": "chat.message", "data": event["payload"]})
