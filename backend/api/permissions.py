from rest_framework import permissions


class IsAuthorOrReadOnly(permissions.BasePermission):
    """Anyone signed in can read; only the author can change or delete."""

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.author_id == request.user.id
