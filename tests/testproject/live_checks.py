#!/usr/bin/env python3
"""
Checks against a real local Keycloak, with the realms of setup_live_realms.py.

Needs the test project running with settings_live (DK_MODE=multi) on 127.0.0.1:8001:

    ../../venv/bin/python manage.py migrate --settings testproject.settings_live
    ../../venv/bin/python manage.py runserver 127.0.0.1:8001 --settings testproject.settings_live

and, for the refresh check, a second server with DK_REFRESH=1 on 127.0.0.1:8002.

Run from tests/testproject: ../../venv/bin/python live_checks.py
The browser is simulated with requests: *.localhost is sent to 127.0.0.1.
"""

import html
import os
import re
import sys
import time

import django
import requests
import urllib3.util.connection

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "testproject.settings_live")
django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402

from django_keycloak.keycloak_admin import KeycloakAdminClient  # noqa: E402
from django_keycloak.models import KeycloakIdentity, KeycloakSession  # noqa: E402
from django_keycloak.services import (  # noqa: E402
    KeycloakUserError,
    create_user_with_temporary_password,
    link_existing_account,
)


_create_connection = urllib3.util.connection.create_connection


def create_connection(address, *args, **kwargs):
    host, port = address
    if host.endswith(".localhost"):
        host = "127.0.0.1"
    return _create_connection((host, port), *args, **kwargs)


urllib3.util.connection.create_connection = create_connection

LIVE = settings.LIVE
ALPHA, BETA = "dk-alpha", "dk-beta"
results = []


def check(name, condition, detail=""):
    results.append(condition)
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


class LocalSession(requests.Session):
    """
    Browsers send Secure cookies to local hosts over http (a secure context); requests
    does not, so Keycloak's login cookies would be lost.
    """

    def prepare_request(self, request):
        for cookie in self.cookies:
            cookie.secure = False
        return super().prepare_request(request)


class Browser:
    def __init__(self, realm, port=8001):
        self.realm = realm
        self.host = LIVE[realm]["host"]
        self.port = port
        self.http = LocalSession()

    def url(self, path, port=None):
        return f"http://{self.host}:{port or self.port}{path}"

    def login(self, username):
        """Log in through Keycloak's form; return the callback's response."""
        page = self.http.get(self.url("/api/oidc/login/"), timeout=20)
        form = re.search(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"', page.text)
        if not form:
            raise AssertionError(f"no Keycloak login form ({page.status_code} {page.url})")
        answer = self.http.post(
            html.unescape(form.group(1)),
            data={"username": username, "password": LIVE[self.realm]["passwords"][username]},
            allow_redirects=False,
            timeout=20,
        )
        location = answer.headers.get("Location", "")
        if "/api/oidc/callback/" not in location:
            raise AssertionError(f"Keycloak did not redirect to the callback ({answer.status_code})")
        return self.http.get(location, allow_redirects=False, timeout=20)

    def session(self, port=None):
        return self.http.get(self.url("/session/", port), timeout=20).json()

    def logout(self, method="post", csrf=True, port=None):
        self.session(port)
        headers = {"X-CSRFToken": self.http.cookies.get("csrftoken")} if csrf else {}
        return self.http.request(
            method, self.url("/api/oidc/logout/", port), headers=headers, timeout=30,
        )


def keycloak_sessions(realm, username):
    client = KeycloakAdminClient(realm)
    user = client.find_user(username=username)
    response = client.session.get(client._url(f"users/{user['id']}/sessions"), timeout=5)
    response.raise_for_status()
    return client, user, response.json()


def wait_until(condition, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.25)
    return condition()


def main():
    User = get_user_model()
    User.objects.filter(username__in=["boss", "ada@example.test", "ada"]).delete()

    # Superuser from the realm role.
    boss = Browser(ALPHA)
    response = boss.login("boss")
    state = boss.session()
    check("role django-superuser makes a superuser at first login",
          response.status_code == 302 and state["is_superuser"] and state["realm"] == ALPHA,
          f"{response.status_code} {state}")

    # Unverified email.
    response = Browser(BETA).login("uma")
    check("unverified email is refused (403)", response.status_code == 403, response.status_code)

    # No identity, USER_CREATION_MODE disabled.
    response = Browser(ALPHA).login("ada")
    check("account without identity is refused (403)", response.status_code == 403,
          response.status_code)

    # Linking existing realm accounts to one Django user.
    ada = User.objects.create(username="ada@example.test", email="ada@example.test")
    alpha_identity = link_existing_account(ada, email="ada@example.test", realm=ALPHA)
    beta_identity = link_existing_account(ada, email="ADA@example.test", realm=BETA)
    check("existing verified accounts of two realms linked to one user",
          alpha_identity and beta_identity
          and sorted(ada.keycloak_identities.values_list("realm", flat=True)) == [ALPHA, BETA])

    try:
        link_existing_account(User.objects.get(username="boss"), email="uma@example.test", realm=BETA)
        check("account with unverified email is not linked", False)
    except KeycloakUserError as exc:
        check("account with unverified email is not linked", True, exc)

    check("email without account in the realm returns None",
          link_existing_account(ada, email="nobody@example.test", realm=BETA) is None)

    # Three sessions of the same user: two in alpha, one in beta.
    a1, a2, b1 = Browser(ALPHA), Browser(ALPHA), Browser(BETA)
    responses = [a1.login("ada"), a2.login("ada"), b1.login("ada")]
    states = [a1.session(), a2.session(), b1.session()]
    check("one user logged in through two realms",
          all(r.status_code == 302 for r in responses)
          and [s["realm"] for s in states] == [ALPHA, ALPHA, BETA]
          and all(s["username"] == "ada@example.test" for s in states),
          [r.status_code for r in responses])

    # A session of alpha presented on a host of beta.
    a3 = Browser(ALPHA)
    a3.login("ada")
    stolen = Browser(BETA)
    stolen.http.cookies.set("sessionid", a3.http.cookies.get("sessionid"), domain=stolen.host)
    check("session of realm alpha is refused on a host of realm beta",
          not stolen.session()["authenticated"] and not a3.session()["authenticated"])

    # Back-channel logout with sid: end one of the two alpha Keycloak sessions.
    client, user, sessions = keycloak_sessions(ALPHA, "ada")
    check("Keycloak has the alpha sessions of ada", len(sessions) >= 2, len(sessions))
    # The Keycloak session a1 logged in with, as recorded at its login.
    a1_sid = KeycloakSession.objects.get(session_key=a1.http.cookies.get("sessionid")).sid
    check("a1's Keycloak session is one of ada's", a1_sid in [item["id"] for item in sessions])
    deleted = client.session.delete(
        f"{client.server}/admin/realms/{ALPHA}/sessions/{a1_sid}", timeout=5,
    )
    ended = wait_until(lambda: not a1.session()["authenticated"])
    check("back-channel logout with sid ends only that Keycloak session's Django session",
          deleted.status_code == 204 and ended
          and a2.session()["authenticated"] and b1.session()["authenticated"],
          deleted.status_code)

    # Logout: POST with CSRF only.
    check("logout by GET is refused (405)", b1.logout(method="get").status_code == 405)
    check("logout without CSRF token is refused (403)", b1.logout(csrf=False).status_code == 403)
    check("user still logged in after refused logouts", b1.session()["authenticated"])

    # Token refresh in the session's realm (server on 8002 with DK_REFRESH=1).
    try:
        before = b1.session()["refreshed_at"]
        time.sleep(1.5)
        after = b1.session(port=8002)
        check("token refresh against the session's realm (beta)",
              after["authenticated"] and after["refreshed_at"] > before, after)
    except requests.ConnectionError:
        check("token refresh against the session's realm (beta)", False, "no server on 8002")

    response = b1.logout()
    _, _, beta_sessions = keycloak_sessions(BETA, "ada")
    _, _, alpha_sessions = keycloak_sessions(ALPHA, "ada")
    check("logout ends the beta Keycloak sessions only",
          response.status_code == 204 and not b1.session()["authenticated"]
          and not beta_sessions and len(alpha_sessions) >= 1 and a2.session()["authenticated"],
          f"{response.status_code} beta={len(beta_sessions)} alpha={len(alpha_sessions)}")

    # Back-channel logout of every session of the user (admin "sign out").
    response = client.session.post(client._url(f"users/{user['id']}/logout"), timeout=5)
    check("Keycloak sign-out of the user ends its alpha Django sessions",
          response.status_code == 204
          and wait_until(lambda: not a2.session()["authenticated"]))

    # A Keycloak account in a second realm for an existing Django user.
    boss_user = User.objects.get(username="boss")
    created = None
    try:
        create_user_with_temporary_password(
            username="boss", email="boss@example.test", password="Temp-Pass-1234!",
            realm=BETA, user=boss_user,
        )
        created = KeycloakAdminClient(BETA).find_user(username="boss")
        check("account created in beta for an existing Django user",
              created is not None
              and boss_user.keycloak_identities.filter(realm=BETA, subject=created["id"]).exists())
    finally:
        if created:
            KeycloakAdminClient(BETA).delete_user(created["id"])
        KeycloakIdentity.objects.filter(user=boss_user, realm=BETA).delete()

    # Password change. Keycloak's cookies are dropped first: with them, Keycloak 26 answers
    # the simulated browser "already_logged_in" (not checked in a real browser).
    changer = Browser(ALPHA)
    changer.login("ada")
    aia = changer.http.get(
        changer.url("/api/oidc/change-password/"), allow_redirects=False,
    ).headers["Location"]
    for cookie in list(changer.http.cookies):
        if cookie.domain == "127.0.0.1":
            changer.http.cookies.clear(cookie.domain, cookie.path, cookie.name)
    password = LIVE[ALPHA]["passwords"]["ada"]
    page = changer.http.get(aia, timeout=20)
    form = re.search(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"', page.text)
    page = changer.http.post(
        html.unescape(form.group(1)), data={"username": "ada", "password": password}, timeout=20,
    )
    form = re.search(r'<form[^>]*id="kc-passwd-update-form"[^>]*action="([^"]+)"', page.text)
    answer = changer.http.post(
        html.unescape(form.group(1)),
        data={"password-new": password, "password-confirm": password},
        allow_redirects=False,
        timeout=20,
    )
    response = changer.http.get(answer.headers["Location"], allow_redirects=False, timeout=20)
    _, _, sessions = keycloak_sessions(ALPHA, "ada")
    check("password change ends the Keycloak and Django sessions, then goes to the login",
          response.status_code == 302 and response.headers["Location"] == "/api/oidc/login/"
          and not changer.session()["authenticated"] and not sessions)

    print(f"\n{results.count(True)} passed, {results.count(False)} failed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
