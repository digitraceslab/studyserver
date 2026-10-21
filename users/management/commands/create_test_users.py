"""Management command to create participant test accounts.

Each account gets the given username, the email <username>@example.com and a
generated password. Accounts are not joined to the study. Prints one
"username : password" line per account.
"""
import secrets

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from users.models import Profile


EMAIL_DOMAIN = 'example.com'


class Command(BaseCommand):
    help = 'Create participant test accounts with generated passwords.'

    def add_arguments(self, parser):
        parser.add_argument('usernames', nargs='+', help='Account names to create.')

    def handle(self, *args, **options):
        usernames = options['usernames']
        existing = list(
            User.objects.filter(username__in=usernames).values_list('username', flat=True)
        )
        if existing:
            raise CommandError(f'Users already exist: {", ".join(existing)}')

        created = []
        with transaction.atomic():
            for username in usernames:
                password = secrets.token_urlsafe(12)
                user = User.objects.create_user(
                    username=username,
                    email=f'{username}@{EMAIL_DOMAIN}',
                    password=password,
                )
                Profile.objects.create(user=user, user_type='participant')
                created.append((username, password))

        for username, password in created:
            self.stdout.write(f'{username} : {password}')
