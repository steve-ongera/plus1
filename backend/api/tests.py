import io
import shutil
import tempfile
from datetime import date, timedelta

from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from . import services
from .models import LinkUp, Moment, Notification, Status, User, Venue

TEMP_MEDIA = tempfile.mkdtemp()


def make_user(username, **extra):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="pass12345!",
        birth_date=date(1995, 1, 1),
        display_name=username.title(),
        **extra,
    )


def png_file(name="pic.png"):
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


class BaseCase(APITestCase):
    def setUp(self):
        cache.clear()  # reset throttling between tests
        self.venue = Venue.objects.create(
            name="Soda Bar", city="Mombasa", latitude=-4.05, longitude=39.67
        )
        self.far_venue = Venue.objects.create(
            name="Nairobi Wine", city="Nairobi", latitude=-1.29, longitude=36.82
        )
        self.u1 = make_user("amina")
        self.u2 = make_user("brian")

    def login(self, user):
        self.client.force_authenticate(user=user)

    def check_in(self, user, venue=None):
        self.login(user)
        res = self.client.post(f"/api/venues/{(venue or self.venue).slug}/check-in/")
        self.assertEqual(res.status_code, 200, res.data)


class AuthTests(BaseCase):
    def test_register_requires_adult(self):
        payload = {
            "username": "kid", "email": "kid@example.com", "password": "pass12345!",
            "birth_date": (timezone.localdate() - timedelta(days=365 * 15)).isoformat(),
        }
        res = self.client.post("/api/auth/register/", payload, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("birth_date", res.data)

    def test_register_and_login_with_email(self):
        payload = {
            "username": "newbie", "email": "Newbie@Example.com", "password": "pass12345!",
            "display_name": "Newbie", "birth_date": "1998-03-04",
        }
        res = self.client.post("/api/auth/register/", payload, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIn("access", res.data)
        self.assertTrue(res.data["user"]["slug"].startswith("newbie-"))

        res = self.client.post(
            "/api/auth/login/", {"username": "newbie@example.com", "password": "pass12345!"},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn("access", res.data)
        self.assertEqual(res.data["user"]["username"], "newbie")

    def test_weak_password_rejected(self):
        payload = {
            "username": "weak", "email": "weak@example.com", "password": "12345678",
            "birth_date": "1998-03-04",
        }
        res = self.client.post("/api/auth/register/", payload, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("password", res.data)

    def test_endpoints_need_auth(self):
        self.assertEqual(self.client.get("/api/venues/").status_code, 401)
        self.assertEqual(self.client.get("/api/health/").status_code, 200)


class VenueAndStatusTests(BaseCase):
    def test_nearby_venues_sorted_and_filtered(self):
        self.login(self.u1)
        res = self.client.get("/api/venues/?lat=-4.05&lng=39.67&radius=10")
        self.assertEqual(res.status_code, 200)
        slugs = [v["slug"] for v in res.data["results"]]
        self.assertEqual(slugs, [self.venue.slug])
        self.assertIsNotNone(res.data["results"][0]["distance_km"])

        res = self.client.get("/api/venues/?lat=-4.05")
        self.assertEqual(res.status_code, 400)

    def test_headcount_ignores_ghosts(self):
        self.check_in(self.u1)
        ghost = make_user("ghosty")
        Status.objects.create(
            user=ghost, intent=Status.Intent.OUT, venue=self.venue
        )
        ghost.is_ghost = True
        ghost.save()
        self.login(self.u2)
        res = self.client.get(f"/api/venues/{self.venue.slug}/")
        self.assertEqual(res.data["headcount"], 1)

    def test_ghost_cannot_check_in(self):
        self.u1.is_ghost = True
        self.u1.save()
        self.login(self.u1)
        res = self.client.post(f"/api/venues/{self.venue.slug}/check-in/")
        self.assertEqual(res.status_code, 400)

    def test_status_roundtrip(self):
        self.login(self.u1)
        res = self.client.get("/api/status/")
        self.assertEqual(res.data["intent"], "IN")
        res = self.client.put(
            "/api/status/", {"intent": "SOON", "vibe": "SODA", "note": "7pm?"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["vibe"], "SODA")
        res = self.client.delete("/api/status/")
        self.assertEqual(res.status_code, 204)

    def test_people_requires_check_in_and_hides_self(self):
        self.check_in(self.u1)
        self.login(self.u2)
        res = self.client.get(f"/api/venues/{self.venue.slug}/people/")
        self.assertEqual(res.status_code, 403)

        self.check_in(self.u2)
        res = self.client.get(f"/api/venues/{self.venue.slug}/people/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual([p["slug"] for p in res.data], [self.u1.slug])

    def test_public_profile_hides_ghost(self):
        self.u1.is_ghost = True
        self.u1.save()
        self.login(self.u2)
        self.assertEqual(self.client.get(f"/api/users/{self.u1.slug}/").status_code, 404)
        self.login(self.u1)
        self.assertEqual(self.client.get(f"/api/users/{self.u1.slug}/").status_code, 200)


class LinkUpTests(BaseCase):
    def send(self, sender, receiver, venue=None, message="hey"):
        self.login(sender)
        return self.client.post(
            "/api/linkups/",
            {"receiver": receiver.slug, "venue": (venue or self.venue).slug, "message": message},
            format="json",
        )

    def test_full_flow_with_notifications_and_chat(self):
        self.check_in(self.u1)
        self.check_in(self.u2)

        res = self.send(self.u1, self.u2)
        self.assertEqual(res.status_code, 201, res.data)
        linkup_id = res.data["id"]
        self.assertEqual(
            Notification.objects.filter(user=self.u2, kind="linkup.received").count(), 1
        )

        # chat is locked until accepted
        res = self.client.post(
            f"/api/linkups/{linkup_id}/messages/", {"body": "hi"}, format="json"
        )
        self.assertEqual(res.status_code, 403)

        # only the receiver can accept
        res = self.client.post(f"/api/linkups/{linkup_id}/accept/")
        self.assertEqual(res.status_code, 403)

        self.login(self.u2)
        res = self.client.get("/api/linkups/?box=incoming")
        self.assertEqual(len(res.data["results"]), 1)
        res = self.client.post(f"/api/linkups/{linkup_id}/accept/")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["status"], "ACCEPTED")
        self.assertEqual(
            Notification.objects.filter(user=self.u1, kind="linkup.accepted").count(), 1
        )

        # chat both ways
        res = self.client.post(
            f"/api/linkups/{linkup_id}/messages/", {"body": "see you there"}, format="json"
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.login(self.u1)
        res = self.client.get(f"/api/linkups/{linkup_id}/messages/")
        self.assertEqual([m["body"] for m in res.data], ["see you there"])
        self.assertEqual(
            Notification.objects.filter(user=self.u1, kind="message.new").count(), 1
        )

        # a stranger cannot see the conversation
        stranger = make_user("stranger")
        self.login(stranger)
        res = self.client.get(f"/api/linkups/{linkup_id}/messages/")
        self.assertEqual(res.status_code, 404)

    def test_must_be_at_same_venue(self):
        self.check_in(self.u1)
        self.check_in(self.u2, self.far_venue)
        res = self.send(self.u1, self.u2)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(LinkUp.objects.count(), 0)

    def test_must_be_checked_in_to_send(self):
        self.check_in(self.u2)
        res = self.send(self.u1, self.u2)
        self.assertEqual(res.status_code, 403)

    def test_duplicate_request_rejected(self):
        self.check_in(self.u1)
        self.check_in(self.u2)
        self.assertEqual(self.send(self.u1, self.u2).status_code, 201)
        self.assertEqual(self.send(self.u1, self.u2).status_code, 409)
        self.assertEqual(self.send(self.u2, self.u1).status_code, 409)

    def test_block_prevents_linkup_and_hides_people(self):
        self.check_in(self.u1)
        self.check_in(self.u2)
        self.login(self.u2)
        res = self.client.post("/api/blocks/", {"user": self.u1.slug}, format="json")
        self.assertEqual(res.status_code, 201, res.data)

        res = self.send(self.u1, self.u2)
        self.assertEqual(res.status_code, 400)

        self.login(self.u1)
        res = self.client.get(f"/api/venues/{self.venue.slug}/people/")
        self.assertEqual(res.data, [])

    def test_block_closes_open_linkup(self):
        self.check_in(self.u1)
        self.check_in(self.u2)
        linkup_id = self.send(self.u1, self.u2).data["id"]
        self.login(self.u2)
        self.client.post(f"/api/linkups/{linkup_id}/accept/")
        self.client.post("/api/blocks/", {"user": self.u1.slug}, format="json")
        self.assertEqual(LinkUp.objects.get(pk=linkup_id).status, "CLOSED")
        res = self.client.post(
            f"/api/linkups/{linkup_id}/messages/", {"body": "hello?"}, format="json"
        )
        self.assertEqual(res.status_code, 403)

    def test_decline_is_silent(self):
        self.check_in(self.u1)
        self.check_in(self.u2)
        linkup_id = self.send(self.u1, self.u2).data["id"]
        self.login(self.u2)
        res = self.client.post(f"/api/linkups/{linkup_id}/decline/")
        self.assertEqual(res.data["status"], "DECLINED")
        self.assertEqual(Notification.objects.filter(user=self.u1).count(), 0)

    def test_notifications_list_and_read(self):
        self.check_in(self.u1)
        self.check_in(self.u2)
        self.send(self.u1, self.u2)
        self.login(self.u2)
        res = self.client.get("/api/notifications/")
        self.assertEqual(res.data["unread_count"], 1)
        res = self.client.post("/api/notifications/read/", {}, format="json")
        self.assertEqual(res.data["updated"], 1)
        res = self.client.get("/api/notifications/")
        self.assertEqual(res.data["unread_count"], 0)

    def test_report(self):
        self.login(self.u1)
        res = self.client.post(
            "/api/reports/",
            {"reported_user": self.u2.slug, "reason": "SPAM", "details": "bot"},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        res = self.client.post(
            "/api/reports/", {"reported_user": self.u1.slug, "reason": "SPAM"}, format="json"
        )
        self.assertEqual(res.status_code, 400)


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class MomentTests(BaseCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def post_moment(self, file):
        return self.client.post(
            "/api/moments/",
            {"venue": self.venue.slug, "caption": "vibes", "file": file},
            format="multipart",
        )

    def test_upload_requires_check_in(self):
        self.login(self.u1)
        res = self.post_moment(png_file())
        self.assertEqual(res.status_code, 403)

    def test_upload_image_and_delete(self):
        self.check_in(self.u1)
        res = self.post_moment(png_file())
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["kind"], "IMAGE")
        slug = res.data["slug"]

        # others can see it; only the author can delete it
        self.login(self.u2)
        res = self.client.get(f"/api/moments/?venue={self.venue.slug}")
        self.assertEqual(len(res.data["results"]), 1)
        self.assertEqual(self.client.delete(f"/api/moments/{slug}/").status_code, 403)
        self.login(self.u1)
        self.assertEqual(self.client.delete(f"/api/moments/{slug}/").status_code, 204)

    def test_upload_rejects_bad_files(self):
        self.check_in(self.u1)
        bad = SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")
        self.assertEqual(self.post_moment(bad).status_code, 400)
        fake = SimpleUploadedFile("pic.png", b"not really a png", content_type="image/png")
        self.assertEqual(self.post_moment(fake).status_code, 400)
        fake_video = SimpleUploadedFile("clip.mp4", b"nope nope nope", content_type="video/mp4")
        self.assertEqual(self.post_moment(fake_video).status_code, 400)

    def test_upload_accepts_video_signature(self):
        self.check_in(self.u1)
        data = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
        clip = SimpleUploadedFile("clip.mp4", data, content_type="video/mp4")
        res = self.post_moment(clip)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["kind"], "VIDEO")


class ExpiryTests(BaseCase):
    def test_expire_stale(self):
        past = timezone.now() - timedelta(hours=1)
        Status.objects.create(
            user=self.u1, intent=Status.Intent.OUT, venue=self.venue, expires_at=past
        )
        Moment.objects.create(
            author=self.u1, venue=self.venue, kind="IMAGE", file="moments/none.png",
            expires_at=past,
        )
        old = LinkUp.objects.create(sender=self.u1, receiver=self.u2, venue=self.venue)
        LinkUp.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=2))

        counts = services.expire_stale()
        self.assertEqual(counts["moments"], 1)
        self.assertEqual(counts["linkups"], 1)
        self.assertFalse(Status.objects.exists())
        self.assertEqual(LinkUp.objects.get(pk=old.pk).status, "EXPIRED")

    def test_expired_status_is_not_active(self):
        Status.objects.create(
            user=self.u1, intent=Status.Intent.OUT, venue=self.venue,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertIsNone(services.get_active_status(self.u1))


class WebSocketTests(TransactionTestCase):
    async def test_notification_is_pushed_live(self):
        from config.asgi import application

        user = await sync_to_async(make_user)("wsuser")
        token = str(AccessToken.for_user(user))

        communicator = WebsocketCommunicator(application, f"/ws/notifications/?token={token}")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        hello = await communicator.receive_json_from()
        self.assertEqual(hello["event"], "connected")

        await sync_to_async(services.notify)(user, "system", "Hello", "Live!", {"x": 1})
        msg = await communicator.receive_json_from(timeout=3)
        self.assertEqual(msg["event"], "notification")
        self.assertEqual(msg["data"]["title"], "Hello")

        await communicator.send_json_to({"type": "ping"})
        self.assertEqual((await communicator.receive_json_from())["event"], "pong")
        await communicator.disconnect()

    async def test_socket_rejects_missing_or_bad_token(self):
        from config.asgi import application

        for path in ("/ws/notifications/", "/ws/notifications/?token=garbage"):
            communicator = WebsocketCommunicator(application, path)
            connected, _ = await communicator.connect()
            self.assertFalse(connected)
