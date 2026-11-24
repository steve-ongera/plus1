"""API views. Thin on purpose: the rules live in services.py."""
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, mixins, permissions, viewsets
from rest_framework import status as http
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from . import services
from .models import Block, LinkUp, Moment, Notification, Status, User, Venue
from .permissions import IsAuthorOrReadOnly
from .serializers import (
    BlockCreateSerializer,
    BlockSerializer,
    LinkUpCreateSerializer,
    LinkUpSerializer,
    LoginSerializer,
    MessageCreateSerializer,
    MessageSerializer,
    MomentSerializer,
    NotificationSerializer,
    PersonSerializer,
    PublicUserSerializer,
    RegisterSerializer,
    ReportSerializer,
    StatusSerializer,
    UserSerializer,
    VenueDetailSerializer,
    VenueSerializer,
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@api_view(["GET"])
@permission_classes([permissions.AllowAny])
@throttle_classes([])
def health(request):
    return Response({"status": "ok", "time": timezone.now().isoformat()})


# ---------------------------------------------------------------------------
# Auth + profile
# ---------------------------------------------------------------------------
class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_scope = "register"

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "user": UserSerializer(user, context={"request": request}).data,
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            },
            status=http.HTTP_201_CREATED,
        )


class LoginView(TokenObtainPairView):
    serializer_class = LoginSerializer
    throttle_scope = "login"


class MeView(generics.RetrieveUpdateDestroyAPIView):
    """Own profile. DELETE removes the account and all its data (GDPR)."""

    serializer_class = UserSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_object(self):
        return self.request.user

    def retrieve(self, request, *args, **kwargs):
        User.objects.filter(pk=request.user.pk).update(last_seen=timezone.now())
        return super().retrieve(request, *args, **kwargs)


class PublicUserView(generics.RetrieveAPIView):
    serializer_class = PublicUserSerializer
    lookup_field = "slug"
    queryset = User.objects.filter(is_active=True)

    def get_object(self):
        user = super().get_object()
        me = self.request.user
        if user.id != me.id and (user.is_ghost or user.id in services.blocked_ids(me)):
            raise NotFound()
        return user


# ---------------------------------------------------------------------------
# Venues
# ---------------------------------------------------------------------------
def _coord(params, name, low, high):
    raw = params.get(name)
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
    except ValueError:
        raise ValidationError({name: "Must be a number."})
    if not low <= value <= high:
        raise ValidationError({name: f"Must be between {low} and {high}."})
    return value


class VenueViewSet(viewsets.ReadOnlyModelViewSet):
    lookup_field = "slug"

    def get_queryset(self):
        return services.venues_with_headcount().prefetch_related("media")

    def get_serializer_class(self):
        return VenueDetailSerializer if self.action == "retrieve" else VenueSerializer

    def list(self, request, *args, **kwargs):
        params = request.query_params
        lat = _coord(params, "lat", -90, 90)
        lng = _coord(params, "lng", -180, 180)
        if (lat is None) != (lng is None):
            raise ValidationError("Send both lat and lng, or neither.")
        radius = None
        if params.get("radius"):
            try:
                radius = float(params["radius"])
            except ValueError:
                raise ValidationError({"radius": "Must be a number (km)."})
            if radius <= 0:
                raise ValidationError({"radius": "Must be greater than 0."})

        venues = services.nearby_venues(lat, lng, radius, params.get("q"))
        page = self.paginate_queryset(venues)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @action(detail=True, methods=["get"])
    def people(self, request, slug=None):
        venue = self.get_object()
        statuses = services.people_at_venue(request.user, venue)
        serializer = PersonSerializer(statuses, many=True, context={"request": request})
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="check-in")
    def check_in(self, request, slug=None):
        venue = self.get_object()
        status = services.check_in(request.user, venue)
        return Response(StatusSerializer(status).data)

    @action(detail=True, methods=["post"], url_path="check-out")
    def check_out(self, request, slug=None):
        venue = self.get_object()
        status = services.check_out(request.user, venue)
        if status is None:
            return Response(_default_status())
        return Response(StatusSerializer(status).data)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
def _default_status():
    return {
        "intent": Status.Intent.IN,
        "vibe": Status.Vibe.ANY,
        "note": "",
        "venue": None,
        "venue_name": None,
        "expires_at": None,
        "is_active": False,
    }


class StatusView(APIView):
    def get(self, request):
        status = services.get_active_status(request.user)
        if not status:
            return Response(_default_status())
        return Response(StatusSerializer(status).data)

    def put(self, request):
        serializer = StatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        status = services.set_status(
            request.user, data["intent"], data.get("vibe"), data.get("note")
        )
        return Response(StatusSerializer(status).data)

    def delete(self, request):
        services.clear_status(request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Moments
# ---------------------------------------------------------------------------
class MomentViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = MomentSerializer
    lookup_field = "slug"
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    permission_classes = [permissions.IsAuthenticated, IsAuthorOrReadOnly]

    def get_throttles(self):
        self.throttle_scope = "upload" if self.action == "create" else None
        return super().get_throttles()

    def get_queryset(self):
        return services.visible_moments(
            self.request.user, self.request.query_params.get("venue")
        )

    def perform_create(self, serializer):
        serializer.save(author=self.request.user)


# ---------------------------------------------------------------------------
# Link ups + chat
# ---------------------------------------------------------------------------
class LinkUpViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = LinkUpSerializer

    def get_throttles(self):
        self.throttle_scope = "linkup" if self.action == "create" else None
        return super().get_throttles()

    def get_queryset(self):
        user = self.request.user
        qs = LinkUp.objects.filter(Q(sender=user) | Q(receiver=user)).select_related(
            "sender", "receiver", "venue"
        )
        if self.action == "list":
            box = self.request.query_params.get("box")
            if box == "incoming":
                qs = qs.filter(receiver=user, status=LinkUp.State.PENDING)
            elif box == "outgoing":
                qs = qs.filter(sender=user, status=LinkUp.State.PENDING)
            elif box == "accepted":
                qs = qs.filter(status=LinkUp.State.ACCEPTED)
        return qs.order_by("-created_at")

    def create(self, request, *args, **kwargs):
        serializer = LinkUpCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        linkup = services.send_linkup(
            request.user, data["receiver"], data["venue"], data.get("message", "")
        )
        return Response(
            LinkUpSerializer(linkup, context=self.get_serializer_context()).data,
            status=http.HTTP_201_CREATED,
        )

    def _respond(self, request, accept):
        linkup = self.get_object()
        linkup = services.respond_to_linkup(request.user, linkup, accept)
        return Response(LinkUpSerializer(linkup, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return self._respond(request, True)

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        return self._respond(request, False)

    @action(detail=True, methods=["get", "post"])
    def messages(self, request, pk=None):
        linkup = self.get_object()  # 404 unless the user is part of this link up

        if request.method == "POST":
            serializer = MessageCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            message = services.send_message(request.user, linkup, serializer.validated_data["body"])
            return Response(MessageSerializer(message).data, status=http.HTTP_201_CREATED)

        qs = linkup.messages.select_related("sender")
        after = request.query_params.get("after")
        if after and after.isdigit():
            items = list(qs.filter(id__gt=int(after)).order_by("id")[:200])
        else:
            items = list(qs.order_by("-id")[:100])[::-1]
        services.mark_messages_read(request.user, linkup)
        return Response(MessageSerializer(items, many=True).data)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
class NotificationListView(generics.ListAPIView):
    serializer_class = NotificationSerializer

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        response.data["unread_count"] = Notification.objects.filter(
            user=request.user, is_read=False
        ).count()
        return response


class NotificationReadView(APIView):
    """POST {"ids": [1, 2]} to mark some as read, or {} to mark all."""

    def post(self, request):
        ids = request.data.get("ids")
        if ids is not None and (
            not isinstance(ids, list) or not all(isinstance(i, int) for i in ids)
        ):
            raise ValidationError({"ids": "Must be a list of numbers."})
        updated = services.mark_notifications_read(request.user, ids)
        return Response({"updated": updated})


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------
class BlockViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = BlockSerializer

    def get_queryset(self):
        return Block.objects.filter(blocker=self.request.user).select_related("blocked")

    def create(self, request, *args, **kwargs):
        serializer = BlockCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        block = services.block_user(request.user, serializer.validated_data["user"])
        return Response(
            BlockSerializer(block, context=self.get_serializer_context()).data,
            status=http.HTTP_201_CREATED,
        )


class ReportCreateView(generics.CreateAPIView):
    serializer_class = ReportSerializer
    throttle_scope = "report"

    def perform_create(self, serializer):
        serializer.save(reporter=self.request.user)
