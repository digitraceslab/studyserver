"""Management command to set up a new deployment: create a superuser and study."""
import getpass

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from data_sources.models import DataSource
from studies.models import Study, StudySourceConfiguration
from users.models import Profile


def get_available_source_types():
    # Use the registered SOURCE_TYPE slugs (stored verbatim in
    # StudySourceConfiguration.source_type), not class names.
    return DataSource.registered_source_types()


class Command(BaseCommand):
    help = 'Set up a new deployment by creating a superuser and study.'

    def handle(self, *args, **options):
        if Study.objects.exists():
            raise CommandError(
                'A study already exists. Only one study per deployment is allowed.'
            )

        # --- Superuser ---
        if not User.objects.filter(is_superuser=True).exists():
            self.stdout.write('\n=== Create Superuser ===\n')

            username = input('Admin username: ')
            email = input('Admin email: ')
            while True:
                password = getpass.getpass('Admin password: ')
                password2 = getpass.getpass('Confirm password: ')
                if password == password2:
                    break
                self.stderr.write('Passwords do not match. Try again.\n')

            User.objects.create_superuser(
                username=username,
                email=email,
                password=password,
            )
            self.stdout.write(self.style.SUCCESS(f'Superuser "{username}" created.\n'))
        else:
            self.stdout.write('Superuser already exists, skipping.\n')

        # --- Study ---
        source_types = get_available_source_types()

        self.stdout.write('\n=== Create Study ===\n')

        title = input('Study title: ')
        description = input('Description: ')
        contact_name = input('Contact person name: ')
        contact_email = input('Contact person email: ')
        config_url = input('Configuration repository URL: ')
        repo_branch = input('Config repo branch (default: main): ').strip() or 'main'

        self.stdout.write(f'\nAvailable data source types: {", ".join(source_types)}')
        self.stdout.write('Enter comma-separated type names, or leave blank to skip.\n')

        study = Study.objects.create(
            title=title,
            description=description,
            contact_name=contact_name,
            contact_email=contact_email,
            config_url=config_url,
            repo_branch=repo_branch,
        )

        # Create source configuration entries
        required_input = input('Required data sources: ')
        for name in [s.strip() for s in required_input.split(',') if s.strip()]:
            if name not in source_types:
                raise CommandError(f'Unknown data source type: {name}')
            StudySourceConfiguration.objects.create(
                study=study,
                source_type=name,
                status='required'
            )

        optional_input = input('Optional data sources: ')
        for name in [s.strip() for s in optional_input.split(',') if s.strip()]:
            if name not in source_types:
                raise CommandError(f'Unknown data source type: {name}')
            StudySourceConfiguration.objects.create(
                study=study,
                source_type=name,
                status='optional'
            )

        self.stdout.write(self.style.SUCCESS(f'\nStudy "{study.title}" created (id={study.id}).\n'))
        self.stdout.write('Deployment setup complete. Add researchers via the admin UI.')
