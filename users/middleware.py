"""Treat an authenticated user without a Profile as if no one were logged in.

Most front-end views assume ``request.user.profile`` exists. A user can be
authenticated yet have no Profile (e.g. a superuser created with
``createsuperuser``), which would otherwise raise ``RelatedObjectDoesNotExist``
and produce a 500. This middleware replaces such a user with ``AnonymousUser``
so ``@login_required`` views redirect to login, exactly as for a logged-out
visitor. The admin is exempt: it authorises by ``is_staff``/``is_superuser`` and
does not need a Profile, so a profile-less superuser keeps admin access.
"""

from django.contrib.auth.models import AnonymousUser

from .models import Profile


class ProfileRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if (
            user.is_authenticated
            and not request.path.startswith("/admin/")
            and not Profile.objects.filter(user=user).exists()
        ):
            request.user = AnonymousUser()
        return self.get_response(request)
