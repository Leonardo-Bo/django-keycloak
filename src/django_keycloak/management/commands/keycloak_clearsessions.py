from django.core.management.base import BaseCommand

from django_keycloak.sessions import clear_expired_records


class Command(BaseCommand):
    help = (
        "Delete the Keycloak session records of Django sessions that have expired. "
        "Run it next to clearsessions."
    )

    def handle(self, *args, **options):
        deleted = clear_expired_records()

        if options["verbosity"] > 0:
            self.stdout.write(f"Deleted {deleted} Keycloak session record(s).")
