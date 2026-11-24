"""All serializers for the Plus1 API."""
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from . import services
from .models import (
    Block,
    LinkUp,
    Message,
    Moment,
    Notification,
    Report,
    Status,
    User,
    Venue,
    VenueMedia,
)


# ---------------------------------------------------------------------------
# Auth + users
# ---------------------------------------------------------------------------
class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    birth_date = serializers.DateField(required=True)

    class Meta:
        model = User
        fields = ("username", "email", "password", "display_name", "birth_date")

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("This username is taken.")
        return value

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_birth_date(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError("Birth date can't be in the future.")
        if services.calculate_age(value) < settings.MIN_AGE:
            raise serializers.ValidationError(
                f"You must be at least {settings.MIN_AGE} years old to use Plus1."
            )
        return value

    def validate(self, attrs):
        candidate = User(username=attrs.get("username", ""), email=attrs.get("email", ""))
        try:
            validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        return User.objects.create_user(password=password, **validated_data)


class LoginSerializer(TokenObtainPairSerializer):
    """Log in with username OR email. Also returns the user profile."""

    def validate(self, attrs):
        identifier = attrs.get(self.username_field, "") or ""
        if "@" in identifier:
            user = User.objects.filter(email__iexact=identifier.strip()).first()
            if user:
                attrs[self.username_field] = user.get_username()
        data = super().validate(attrs)
        data["user"] = UserSerializer(self.user, context=self.context).data
        return data


class UserSerializer(serializers.ModelSerializer):
    """The signed-in user's own profile (`/api/me/`)."""

    age = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id", "slug", "username", "email", "display_name", "bio", "avatar",
            "birth_date", "age", "is_ghost", "role", "date_joined",
        )
        read_only_fields = (
            "id", "slug", "username", "email", "birth_date", "role", "date_joined",
        )
        extra_kwargs = {
            "avatar": {
                "required": False,
                "allow_null": True,
                "validators": [services.validate_image],
            },
        }


class PublicUserSerializer(serializers.ModelSerializer):
    """What other people may see about a user."""

    class Meta:
        model = User
        fields = ("slug", "display_name", "bio", "avatar")


# ---------------------------------------------------------------------------
# Venues
# ---------------------------------------------------------------------------
class VenueMediaSerializer(serializers.ModelSerializer):
    class Meta:
        model = VenueMedia
        fields = ("id", "kind", "file", "caption", "created_at")


class VenueSerializer(serializers.ModelSerializer):
    headcount = serializers.IntegerField(read_only=True, default=0)
    distance_km = serializers.FloatField(read_only=True, default=None)

    class Meta:
        model = Venue
        fields = (
            "slug", "name", "address", "city", "country", "latitude", "longitude",
            "cover_image", "is_sponsored", "headcount", "distance_km",
        )


class VenueDetailSerializer(VenueSerializer):
    media = VenueMediaSerializer(many=True, read_only=True)

    class Meta(VenueSerializer.Meta):
        fields = VenueSerializer.Meta.fields + ("description", "media")


# ---------------------------------------------------------------------------
# Status + people at a venue
# ---------------------------------------------------------------------------
class StatusSerializer(serializers.ModelSerializer):
    venue = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    venue_name = serializers.CharField(source="venue.name", read_only=True, default=None)
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = Status
        fields = ("intent", "vibe", "note", "venue", "venue_name", "expires_at", "is_active")
        read_only_fields = ("expires_at",)
        extra_kwargs = {"intent": {"required": True}}


class PersonSerializer(serializers.ModelSerializer):
    """A person who is checked in at a venue (built from their Status)."""

    slug = serializers.CharField(source="user.slug", read_only=True)
    display_name = serializers.CharField(source="user.display_name", read_only=True)
    bio = serializers.CharField(source="user.bio", read_only=True)
    avatar = serializers.ImageField(source="user.avatar", read_only=True)

    class Meta:
        model = Status
        fields = ("slug", "display_name", "bio", "avatar", "intent", "vibe", "note", "updated_at")


# ---------------------------------------------------------------------------
# Moments
# ---------------------------------------------------------------------------
class MomentSerializer(serializers.ModelSerializer):
    author = PublicUserSerializer(read_only=True)
    venue = serializers.SlugRelatedField(
        slug_field="slug", queryset=Venue.objects.filter(is_active=True)
    )
    venue_name = serializers.CharField(source="venue.name", read_only=True)

    class Meta:
        model = Moment
        fields = (
            "slug", "author", "venue", "venue_name", "kind", "file", "caption",
            "created_at", "expires_at",
        )
        read_only_fields = ("slug", "kind", "created_at", "expires_at")

    def validate_file(self, value):
        services.validate_media(value)  # raises a Django ValidationError if bad
        return value

    def validate(self, attrs):
        request = self.context["request"]
        services.ensure_checked_in(request.user, attrs["venue"])
        attrs["kind"] = services.media_kind_from_name(attrs["file"].name)
        return attrs


# ---------------------------------------------------------------------------
# Link ups + chat
# ---------------------------------------------------------------------------
class LinkUpSerializer(serializers.ModelSerializer):
    sender = PublicUserSerializer(read_only=True)
    receiver = PublicUserSerializer(read_only=True)
    venue = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    venue_name = serializers.CharField(source="venue.name", read_only=True)

    class Meta:
        model = LinkUp
        fields = (
            "id", "sender", "receiver", "venue", "venue_name", "message",
            "status", "created_at", "responded_at",
        )


class LinkUpCreateSerializer(serializers.Serializer):
    receiver = serializers.SlugRelatedField(
        slug_field="slug", queryset=User.objects.filter(is_active=True)
    )
    venue = serializers.SlugRelatedField(
        slug_field="slug", queryset=Venue.objects.filter(is_active=True)
    )
    message = serializers.CharField(max_length=200, required=False, allow_blank=True)


class MessageSerializer(serializers.ModelSerializer):
    sender_slug = serializers.CharField(source="sender.slug", read_only=True)
    sender_name = serializers.CharField(source="sender.display_name", read_only=True)

    class Meta:
        model = Message
        fields = ("id", "linkup", "sender_slug", "sender_name", "body", "created_at", "read_at")


class MessageCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=services.MAX_MESSAGE_LENGTH)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ("id", "kind", "title", "body", "data", "is_read", "created_at")


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------
class BlockSerializer(serializers.ModelSerializer):
    blocked = PublicUserSerializer(read_only=True)

    class Meta:
        model = Block
        fields = ("id", "blocked", "created_at")


class BlockCreateSerializer(serializers.Serializer):
    user = serializers.SlugRelatedField(
        slug_field="slug", queryset=User.objects.filter(is_active=True)
    )


class ReportSerializer(serializers.ModelSerializer):
    reported_user = serializers.SlugRelatedField(
        slug_field="slug", queryset=User.objects.filter(is_active=True)
    )

    class Meta:
        model = Report
        fields = ("id", "reported_user", "reason", "details", "status", "created_at")
        read_only_fields = ("id", "status", "created_at")

    def validate_reported_user(self, value):
        if value.id == self.context["request"].user.id:
            raise serializers.ValidationError("You can't report yourself.")
        return value
