from django.test import TestCase, Client
from django.test import override_settings
from django.urls import reverse
from users.models import Profile
from rest_framework.authtoken.models import Token
from studies.models import Study, Consent, StudySourceConfiguration
from django.utils import timezone
from django.contrib.auth.models import User
from django.urls import path
from django.contrib import messages
from django.http import HttpResponseRedirect
from study_server.urls import urlpatterns as real_urlpatterns
from django.contrib.auth.models import AnonymousUser, User as DjangoUser, Group
import uuid
from users.views import get_past_consents

def test_message_view(request):
    messages.info(request, 'This is an info message.')
    return HttpResponseRedirect(reverse('dashboard'))

urlpatterns = real_urlpatterns + [
    path('test-message/', test_message_view, name='test_message_view'),
]

class BaseTestCase(TestCase):
    def setUp(self):
        super().setUp()
        # Provide a safe `profile` attribute on AnonymousUser during tests
        def _anon_profile(self):
            if not hasattr(self, '_test_profile'):
                user = DjangoUser.objects.create_user(username=f'anon_{uuid.uuid4().hex}', password='pass')
                self._test_profile = Profile.objects.create(user=user, user_type='participant')
            return self._test_profile
        AnonymousUser.profile = property(_anon_profile)

    def tearDown(self):
        # Remove the injected property to avoid leaking into other tests
        try:
            delattr(AnonymousUser, 'profile')
        except Exception:
            pass
        super().tearDown()


class StaticPagesTest(BaseTestCase):
    def test_terms_of_service_renders(self):
        response = self.client.get(reverse('terms_of_service'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(len(response.content.decode().strip()) > 0)

    def test_privacy_statement_renders(self):
        response = self.client.get(reverse('privacy_statement'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(len(response.content.decode().strip()) > 0)

class HomeViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.study = Study.objects.create(
            title='Test Study',
            description='A test study',
            config_url="test_url",
            study_page_html='<h2>Study Front Page</h2>',
        )
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.profile = Profile.objects.create(
            user=self.user,
            user_type='participant'
        )

    def test_home_redirects_to_dashboard_if_in_study(self):
        Consent.objects.create(
            participant=self.profile,
            study = self.study,
            consent_date=timezone.now()
        )
        self.client.login(username='testuser', password='testpass')
        response = self.client.get('/')
        self.assertRedirects(response, reverse('dashboard'))

    def test_home_renders_study_detail_if_not_in_study(self):
        self.client.login(username='testuser', password='testpass')
        response = self.client.get('/')
        self.assertContains(response, "Study Front Page")
    
class SignupViewTest(TestCase):
    def setUp(self):
        self.client = Client()

    def test_signup_page_loads(self):
        response = self.client.post(reverse('signup'), {
            'username': 'newuser',
            'password1': 'StrongPass123',
            'password2': 'StrongPass123',
        })
        self.assertRedirects(response, reverse('login'))
        self.assertTrue(User.objects.filter(username='newuser').exists())


class SignupResearcherViewTest(TestCase):
    def setUp(self):
        self.client = Client()

    def test_signup_researcher_page_loads(self):
        response = self.client.get(reverse('signup_researcher'))
        self.assertEqual(response.status_code, 200)

    def test_signup_researcher_creates_user(self):
        response = self.client.post(reverse('signup_researcher'), {
            'username': 'newresearcher',
            'password1': 'StrongPass123',
            'password2': 'StrongPass123',
        })
        self.assertRedirects(response, reverse('login'))
        self.assertTrue(User.objects.filter(username='newresearcher').exists())


class ManageTokenViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='apitest', password='pass')
        self.profile = Profile.objects.create(user=self.user, user_type='participant')
        #self.token = Token.objects.create(user=self.user)
        self.client.login(username='apitest', password='pass')

    def test_token_is_shown(self):
        response = self.client.get(reverse('manage_token'))
        self.assertEqual(response.status_code, 200)
        token = Token.objects.get(user=self.user).key
        self.assertIn(token, response.content.decode())

    def test_token_refresh(self):
        old_token = Token.objects.get(user=self.user).key
        response = self.client.post(reverse('manage_token'), {'regenerate': '1'}, follow=True)
        new_token = Token.objects.get(user=self.user).key
        self.assertNotEqual(old_token, new_token)
        self.assertIn(new_token, response.content.decode())


class DashboardViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='testuser', password='pass')
        self.profile = Profile.objects.create(user=self.user, user_type='participant')
        self.study = Study.objects.create(title="PolAlpha Study")
        self.consent = Consent.objects.create(participant=self.profile, study=self.study, consent_date=timezone.now())

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)

    def test_dashboard_renders_for_participant(self):
        self.client.login(username='testuser', password='pass')
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, self.study.title)

    @override_settings(ROOT_URLCONF=__name__)
    def test_dashboard_renders_info_message(self):
        """ use the message framework to show a message """
        self.client.login(username='testuser', password='pass')
        response = self.client.get(reverse('test_message_view'), follow=True)
        self.assertContains(response, 'This is an info message.')


class ResearcherDashboardViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='researcher', password='pass')
        self.profile = Profile.objects.create(user=self.user, user_type='researcher')
        self.study = Study.objects.create(title="Research Study")
        self.study.researchers.add(self.profile)
        self.client.login(username='researcher', password='pass')
    
    def test_dashboard_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse('researcher_dashboard'))
        self.assertEqual(response.status_code, 302)

    def test_dashboard_renders_for_researcher(self):
        response = self.client.get(reverse('researcher_dashboard'))
        self.assertContains(response, self.study.title)


class ProfileModelTest(TestCase):
    def test_profile_str(self):
        user = User.objects.create_user(username='alice', password='pass')
        profile = Profile.objects.create(user=user, user_type='participant')
        self.assertEqual(str(profile), 'alice - participant')

    def test_token_auto_created_on_user_creation(self):
        user = User.objects.create_user(username='bob', password='pass')
        self.assertTrue(Token.objects.filter(user=user).exists())

    def test_profile_cascade_deletes_with_user(self):
        user = User.objects.create_user(username='charlie', password='pass')
        Profile.objects.create(user=user, user_type='participant')
        user.delete()
        self.assertFalse(Profile.objects.filter(user__username='charlie').exists())


class SignupViewExtendedTest(TestCase):
    def test_signup_get_renders_form(self):
        response = self.client.get(reverse('signup'))
        self.assertEqual(response.status_code, 200)

    def test_signup_password_mismatch(self):
        response = self.client.post(reverse('signup'), {
            'username': 'newuser',
            'password1': 'StrongPass123',
            'password2': 'WrongPass456',
        })
        self.assertEqual(response.status_code, 200)  # stays on page
        self.assertFalse(User.objects.filter(username='newuser').exists())

    def test_signup_duplicate_username(self):
        User.objects.create_user(username='existing', password='pass')
        response = self.client.post(reverse('signup'), {
            'username': 'existing',
            'password1': 'StrongPass123',
            'password2': 'StrongPass123',
        })
        self.assertEqual(response.status_code, 200)  # stays on page
        self.assertEqual(User.objects.filter(username='existing').count(), 1)

    def test_signup_creates_participant_profile(self):
        self.client.post(reverse('signup'), {
            'username': 'partuser',
            'password1': 'StrongPass123',
            'password2': 'StrongPass123',
        })
        user = User.objects.get(username='partuser')
        self.assertEqual(user.profile.user_type, 'participant')


class SignupResearcherExtendedTest(TestCase):
    def setUp(self):
        Group.objects.get_or_create(name='Researchers')

    def test_signup_researcher_creates_researcher_profile(self):
        self.client.post(reverse('signup_researcher'), {
            'username': 'res1',
            'password1': 'StrongPass123',
            'password2': 'StrongPass123',
        })
        user = User.objects.get(username='res1')
        self.assertEqual(user.profile.user_type, 'researcher')
        self.assertTrue(user.is_staff)

    def test_signup_researcher_adds_to_group(self):
        self.client.post(reverse('signup_researcher'), {
            'username': 'res2',
            'password1': 'StrongPass123',
            'password2': 'StrongPass123',
        })
        user = User.objects.get(username='res2')
        self.assertTrue(user.groups.filter(name='Researchers').exists())


class LoginViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='loginuser', password='correctpass')
        Profile.objects.create(user=self.user, user_type='participant')

    def test_login_get_renders_form(self):
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)

    def test_login_valid_credentials_redirects(self):
        response = self.client.post(reverse('login'), {
            'username': 'loginuser',
            'password': 'correctpass',
        })
        self.assertRedirects(response, reverse('home'))

    def test_login_invalid_credentials_shows_error(self):
        response = self.client.post(reverse('login'), {
            'username': 'loginuser',
            'password': 'wrongpass',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Invalid username or password')


class HomeViewExtendedTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.study = Study.objects.create(
            title='Domain Study',
            description='A study',
            config_url='test_url',
            study_page_html='<h2>Study Front Page</h2>',
        )
        self.user = User.objects.create_user(username='homeuser', password='pass')
        self.profile = Profile.objects.create(user=self.user, user_type='participant')

    def test_home_no_study_shows_home(self):
        self.study.delete()
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

    def test_home_unauthenticated_shows_study_detail(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        # Should not redirect to dashboard
        self.assertNotEqual(response.status_code, 302)

    def test_home_revoked_consent_shows_study_detail(self):
        source_config = StudySourceConfiguration.objects.create(study=self.study, source_type='aware', status='required')
        Consent.objects.create(participant=self.profile, study=self.study, source_configuration=source_config, consent_date=timezone.now(), revocation_date=timezone.now())
        self.client.login(username='homeuser', password='pass')
        response = self.client.get('/')
        # Revoked consent should NOT trigger redirect to dashboard
        self.assertNotEqual(response.status_code, 302)


class DashboardExtendedTest(TestCase):
    def test_dashboard_redirects_researcher_to_researcher_dashboard(self):
        user = User.objects.create_user(username='resuser', password='pass')
        Profile.objects.create(user=user, user_type='researcher')
        self.client.login(username='resuser', password='pass')
        response = self.client.get(reverse('dashboard'))
        self.assertRedirects(response, reverse('researcher_dashboard'))

    def test_dashboard_shows_revoked_consent_in_past(self):
        user = User.objects.create_user(username='pastuser', password='pass')
        profile = Profile.objects.create(user=user, user_type='participant')
        study = Study.objects.create(title='Past Study', config_url='test')
        Consent.objects.create(
            participant=profile,
            study=study,
            consent_date=timezone.now(),
            revocation_date=timezone.now(),
            source_type='SomeSource'
        )
        self.client.login(username='pastuser', password='pass')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(len(response.context['past_consents']), 1)


class ResearcherDashboardExtendedTest(TestCase):
    def test_researcher_dashboard_denies_participant(self):
        user = User.objects.create_user(username='partuser2', password='pass')
        Profile.objects.create(user=user, user_type='participant')
        self.client.login(username='partuser2', password='pass')
        response = self.client.get(reverse('researcher_dashboard'))
        self.assertRedirects(response, reverse('dashboard'))

    def test_researcher_dashboard_no_studies(self):
        user = User.objects.create_user(username='emptyres', password='pass')
        Profile.objects.create(user=user, user_type='researcher')
        self.client.login(username='emptyres', password='pass')
        response = self.client.get(reverse('researcher_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['studies_data']), 0)

    def test_researcher_dashboard_shows_participant_stats(self):
        researcher_user = User.objects.create_user(username='statres', password='pass')
        researcher_profile = Profile.objects.create(user=researcher_user, user_type='researcher')
        study = Study.objects.create(title='Stats Study', config_url='test')
        study.researchers.add(researcher_profile)

        part_user = User.objects.create_user(username='statpart', password='pass')
        part_profile = Profile.objects.create(user=part_user, user_type='participant')
        source_config = StudySourceConfiguration.objects.create(study=study, source_type='aware', status='required')
        Consent.objects.create(participant=part_profile, study=study, source_configuration=source_config, consent_date=timezone.now(), source_type='SomeSource', is_complete=True)

        self.client.login(username='statres', password='pass')
        response = self.client.get(reverse('researcher_dashboard'))
        self.assertEqual(len(response.context['studies_data']), 1)
        self.assertEqual(len(response.context['studies_data'][0]['participants']), 1)


class ParticipantDetailTest(TestCase):
    def setUp(self):
        self.study = Study.objects.create(title='Detail Study', config_url='test')

        self.researcher_user = User.objects.create_user(username='detres', password='pass')
        self.researcher_profile = Profile.objects.create(user=self.researcher_user, user_type='researcher')
        self.study.researchers.add(self.researcher_profile)

        self.part_user = User.objects.create_user(username='detpart', password='pass')
        self.part_profile = Profile.objects.create(user=self.part_user, user_type='participant')
        self.consent = source_config = StudySourceConfiguration.objects.create(study=self.study, source_type='aware', status='required')
        Consent.objects.create(participant=self.part_profile, study=self.study, source_configuration=source_config, consent_date=timezone.now(), source_type='SomeSource')
        self.url = reverse('participant_detail', args=[self.study.id, self.part_profile.id])

    def test_participant_detail_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_participant_detail_denies_participant_user(self):
        self.client.login(username='detpart', password='pass')
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse('dashboard'))

    def test_participant_detail_denies_unrelated_researcher(self):
        other_user = User.objects.create_user(username='otherres', password='pass')
        Profile.objects.create(user=other_user, user_type='researcher')
        self.client.login(username='otherres', password='pass')
        response = self.client.get(self.url, follow=True)
        self.assertRedirects(response, reverse('researcher_dashboard'))

    def test_participant_detail_allows_assigned_researcher(self):
        self.client.login(username='detres', password='pass')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_participant_detail_allows_superuser(self):
        su = User.objects.create_superuser(username='super', password='pass')
        Profile.objects.create(user=su, user_type='researcher')
        self.client.login(username='super', password='pass')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_participant_detail_404_invalid_study(self):
        self.client.login(username='detres', password='pass')
        url = reverse('participant_detail', args=[9999, self.part_profile.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_participant_detail_404_invalid_participant(self):
        self.client.login(username='detres', password='pass')
        url = reverse('participant_detail', args=[self.study.id, 9999])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


class ManageTokenExtendedTest(TestCase):
    def test_manage_token_requires_login(self):
        response = self.client.get(reverse('manage_token'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_manage_token_shows_researcher_flag(self):
        user = User.objects.create_user(username='tokenres', password='pass')
        Profile.objects.create(user=user, user_type='researcher')
        self.client.login(username='tokenres', password='pass')
        response = self.client.get(reverse('manage_token'))
        self.assertTrue(response.context['is_researcher'])


class MyDataApiTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='apiuser', password='pass')
        self.profile = Profile.objects.create(user=self.user, user_type='participant')
        self.token = Token.objects.get(user=self.user)

    def test_my_data_api_requires_auth(self):
        response = self.client.get(reverse('my_data_api'))
        self.assertEqual(response.status_code, 401)

    def test_my_data_api_token_auth(self):
        response = self.client.get(
            reverse('my_data_api'),
            HTTP_AUTHORIZATION=f'Token {self.token.key}'
        )
        self.assertEqual(response.status_code, 200)

    def test_my_data_api_returns_json_by_default(self):
        response = self.client.get(
            reverse('my_data_api'),
            HTTP_AUTHORIZATION=f'Token {self.token.key}'
        )
        data = response.json()
        self.assertIn('data_count', data)
        self.assertIn('data_types', data)
        self.assertIn('data', data)

    def test_my_data_api_no_sources_returns_empty(self):
        response = self.client.get(
            reverse('my_data_api'),
            HTTP_AUTHORIZATION=f'Token {self.token.key}'
        )
        data = response.json()
        self.assertEqual(data['data_count'], 0)
        self.assertEqual(data['data'], [])


class GetPastConsentsTest(TestCase):
    """Tests for the get_past_consents helper and display-type resolution (Fixes B+C)."""

    def setUp(self):
        self.user = User.objects.create_user(username='past_consent_user', password='pass')
        self.profile = Profile.objects.create(user=self.user, user_type='participant')
        self.study = Study.objects.create(title='Past Study', config_url='test')

    def test_revoked_niimport_google_portability_shows_display_type(self):
        """A revoked niimport consent with google_portability configuration
        should show 'Google Portability Data', not the raw slug."""
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='niimport',
            configuration={'niimport_source_type': 'google_portability'},
            consent_date=timezone.now(),
            revocation_date=timezone.now(),
        )
        result = get_past_consents(self.profile)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['type_name'], 'Google Portability Data')

    def test_unknown_source_type_returns_title_case_fallback(self):
        """An unknown source_type slug should produce a title-cased fallback,
        not raise an exception (regression: apps.get_model previously crashed)."""
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='SomeSource',
            consent_date=timezone.now(),
            revocation_date=timezone.now(),
        )
        result = get_past_consents(self.profile)
        self.assertEqual(len(result), 1)
        # Should not crash; the fallback is title-cased
        self.assertIsNotNone(result[0]['type_name'])

    def test_dashboard_past_consents_renders_without_crash(self):
        """Dashboard should not crash when past consents have unknown source_type (Fixes B+C e2e)."""
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='SomeSource',
            consent_date=timezone.now(),
            revocation_date=timezone.now(),
        )
        self.client.login(username='past_consent_user', password='pass')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
