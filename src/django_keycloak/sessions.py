from django.contrib.sessions.models import Session
from django.http import HttpRequest
from django.utils import timezone

from .models import KeycloakIdentity, KeycloakSession


def forget_session(request: HttpRequest) -> None:
    """Flush the Django session and drop its back-channel logout record."""
    if request.session.session_key:
        KeycloakSession.objects.filter(
            session_key=request.session.session_key,
        ).delete()

    request.session.flush()


def end_sessions(identity: KeycloakIdentity, sid: str | None = None) -> None:
    """
    Delete the Django sessions of a Keycloak identity.

    With a Keycloak session id (``sid``) only the Django sessions opened in that Keycloak
    session are deleted; without it, all the sessions of the identity. Sessions of the same
    Django user opened in another realm are not touched.
    """
    records = KeycloakSession.objects.filter(identity=identity)

    if sid:
        records = records.filter(sid=sid)

    session_keys = list(records.values_list("session_key", flat=True))

    Session.objects.filter(session_key__in=session_keys).delete()
    records.delete()


def clear_expired_records() -> int:
    """
    Delete the records of Django sessions that have expired or no longer exist.

    Returns the number of records deleted.
    """
    alive = Session.objects.filter(
        expire_date__gt=timezone.now(),
    ).values("session_key")

    deleted, _ = KeycloakSession.objects.exclude(session_key__in=alive).delete()

    return deleted
