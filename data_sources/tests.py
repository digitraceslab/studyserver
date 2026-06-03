from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from .models import AwareDataSource, JsonUrlDataSource, NiimportDataSource
from .models.base import DataSource
from django.core.exceptions import ValidationError
from studies.models import Study, Consent, StudySourceConfiguration
from django.utils import timezone
from users.models import Profile
from django import forms
import uuid
import tempfile
import os
from datetime import date, datetime
from django.test import override_settings
import requests
from data_sources.models import portability_client

import pandas as pd
import io
from data_sources.models import db_connector



class AddDataSourceViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.profile = Profile.objects.create(user=self.user)
        self.client.login(username='testuser', password='testpass')


class DbConnectorTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.profile = Profile.objects.create(user=self.user)
        self.client.login(username='testuser', password='testpass')

    def test_get_device_ids_for_label_empty(self):
        self.assertEqual(db_connector.get_device_ids_for_label(''), [])

    def test_query_aware_data_returns_transformed_rows(self):
        # Prepare fake mysql connector behavior
        class FakeCursor:
            def __init__(self, rows=None, dictionary=False):
                self._rows = rows or []
                self.dictionary = dictionary
                self.last_query = None

            def execute(self, q, params=None):
                self.last_query = (q, params)
                # Heuristics to provide appropriate fake results depending on query
                qstr = str(q).lower()
                if 'select id, device_uuid from device_lookup' in qstr:
                    # return device_lookup mapping as dicts when dictionary=True
                    # If params provided, filter the available mappings to those device_uuid values
                    if params and self._rows:
                        # params may be tuple of device_uuid strings
                        vals = set(params) if isinstance(params, (list, tuple)) else {params}
                        if self.dictionary:
                            self._rows = [r for r in self._rows if str(r.get('device_uuid')) in set(map(str, vals))]
                        else:
                            self._rows = [tuple([r.get('id'), r.get('device_uuid')]) for r in self._rows if str(r.get('device_uuid')) in set(map(str, vals))]
                    else:
                        self._rows = [{'id': 42, 'device_uuid': 'dev-uuid'}] if self.dictionary else [(42, 'dev-uuid')]
                elif 'show tables' in qstr:
                    # handled by separate cursor in FakeConnection
                    pass
                elif '_transformed' in qstr:
                    # transformed table query -> return rows with device_uid
                    # If the cursor has preloaded transformed rows, use them
                    src_rows = getattr(self, '_transformed_rows', None)
                    if src_rows:
                        # If params are provided (e.g., device_uid filter), only return matching rows
                        if params:
                            def match_param(r, p):
                                try:
                                    return int(p) == int(r.get('device_uid'))
                                except Exception:
                                    return str(p) == str(r.get('device_uid'))

                            # params may be a single value or sequence
                            param_vals = params if isinstance(params, (list, tuple)) else (params,)
                            filtered = [r for r in src_rows for p in param_vals if match_param(r, p)]
                        else:
                            filtered = list(src_rows)

                        if self.dictionary:
                            self._rows = filtered
                        else:
                            self._rows = [(r.get('device_uid'), r.get('val')) for r in filtered]
                    else:
                        if self.dictionary:
                            self._rows = [{'device_uid': 42, 'val': 1}]
                        else:
                            self._rows = [(42, 1)]
                elif 'select' in qstr and '_transformed' not in qstr and 'device_lookup' not in qstr and 'show tables' not in qstr:
                    # non-transformed table -> device_id is a string (not device_uid)
                    if self.dictionary:
                        # return a distinct value to ensure these rows are distinguishable
                        self._rows = [{'device_id': 'dev-uuid', 'val': 999}]
                    else:
                        self._rows = [('dev-uuid', 999)]
                else:
                    self._rows = []

            def fetchall(self):
                return self._rows

            def fetchone(self):
                return None

            def close(self):
                pass

        class FakeConnection:
            def __init__(self):
                # first cursor used for SHOW TABLES: include both raw and transformed tables
                self._first = FakeCursor(rows=[('battery',), ('battery_transformed',), ('other_table',)])
                # device_lookup mapping and other dictionary cursor results
                self._second = FakeCursor(rows=[{'id': 42, 'device_uuid': 'dev-uuid'}, {'id': 99, 'device_uuid': 'other-uuid'}], dictionary=True)
                # transformed rows to be returned by _run_aware_table_query (contains two device_uids)
                self._transformed_rows = [
                    {'device_uid': 42, 'val': 1},
                    {'device_uid': 99, 'val': 2}
                ]

            def cursor(self, dictionary=False):
                if not dictionary:
                    return self._first
                # For dictionary cursor, we need a cursor that will return mappings
                # when a dictionary cursor is requested, return a cursor capable of
                # returning both the device_lookup mappings and transformed rows
                cur = FakeCursor(rows=self._second._rows, dictionary=True)
                # store transformed rows on the cursor for execute to pick up when needed
                cur._transformed_rows = list(self._transformed_rows)
                return cur

            def close(self):
                pass

        def fake_connect(*args, **kwargs):
            return FakeConnection()

        # Patch get_device_ids_for_label to return a device id
        with patch('data_sources.models.db_connector.mysql.connector.connect', side_effect=fake_connect):
            # Also patch get_device_ids_for_label to return a device uuid so query proceeds
            with patch('data_sources.models.db_connector.get_device_ids_for_label', return_value=['dev-uuid']):
                # Query for device 'dev-uuid' should return only transformed rows
                rows = db_connector.query_aware_data('SELECT *', 'label-1', 'battery', limit=10)
                self.assertIsInstance(rows, list)
                # Should only return transformed-row for device_uid 42 -> dev-uuid
                self.assertEqual(len(rows), 1)
                first = rows[0]
                self.assertIn('device_id', first)
                self.assertEqual(first.get('device_id'), 'dev-uuid')
                self.assertEqual(first.get('val'), 1)

                # Query for a different device should return the other transformed row
                with patch('data_sources.models.db_connector.get_device_ids_for_label', return_value=['other-uuid']):
                    rows2 = db_connector.query_aware_data('SELECT *', 'label-1', 'battery', limit=10)
                    self.assertIsInstance(rows2, list)
                    self.assertEqual(len(rows2), 1)
                    first2 = rows2[0]
                    self.assertEqual(first2.get('device_id'), 'other-uuid')
                    self.assertEqual(first2.get('val'), 2)

                # Ensure non-transformed table rows (val 999) are not returned
                vals = [r.get('val') for r in rows]
                self.assertNotIn(999, vals)

                # Test count function uses query_aware_data and returns 0 or number
                with patch('data_sources.models.db_connector.query_aware_data', return_value=[{'row_count': 5}]):
                    cnt = db_connector.get_aware_count('label-1', 'battery')
                    self.assertEqual(cnt, 5)
        self.client.login(username='testuser', password='testpass')

    def test_auto_create_data_source_with_only_name_field(self):
        response = self.client.get(reverse('add_data_source', args=['aware']))
        self.assertEqual(response.status_code, 302)
        source = AwareDataSource.objects.filter(profile=self.profile).latest('id')
        expected_url = reverse('instructions', args=[source.id])
        self.assertEqual(response.url, expected_url)
        self.assertTrue(
            AwareDataSource.objects.filter(profile=self.profile, name__istartswith='aware').exists()
        )
        self.assertTrue(source.name.lower().startswith('aware'))

    def test_render_form_when_extra_fields(self):
        # Patch the helper that decides whether the form has only the name
        with patch('data_sources.views.form_has_only_name_field', return_value=False):
            response = self.client.get(reverse('add_data_source', args=['aware']))
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, 'data_sources/add_data_source.html')
            self.assertContains(response, 'name')

    def test_create_data_source_via_post(self):
        response = self.client.post(reverse('add_data_source', args=['aware']), {'name': 'My New Source'})
        self.assertEqual(response.status_code, 302)
        source = AwareDataSource.objects.filter(profile=self.profile).latest('id')
        expected_url = reverse('instructions', args=[source.id])
        self.assertEqual(response.url, expected_url)
        self.assertTrue(
            AwareDataSource.objects.filter(profile=self.profile, name='My New Source').exists()
        )
        self.assertEqual(source.name, 'My New Source')

    def test_create_json_data_source_with_extra_field(self):
        post_data = {
            'name': 'My JSON Source',
            'url': 'https://example.com/data.json'
        }
        response = self.client.post(reverse('add_data_source', args=['json_url']), post_data)
        self.assertEqual(response.status_code, 302)

        source = JsonUrlDataSource.objects.filter(profile=self.profile, name='My JSON Source').latest('id')
        self.assertEqual(response.url, reverse('dashboard'))
        self.assertEqual(source.url, 'https://example.com/data.json')
    



class DataSourcesViewsTest(TestCase):
    def setUp(self):
        # Create a test user and profile
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.profile = Profile.objects.create(user=self.user)
        self.client.login(username='testuser', password='testpass')
        self.source = AwareDataSource.objects.create(
            profile=self.profile,
            name='Test Aware Source'
        )

    def test_user_cannot_edit_others_source(self):
        """Tests that a user gets a 404 when trying to edit another user's source."""
        user2 = User.objects.create_user(username='user2', password='password123')
        Profile.objects.create(user=user2)
        self.client.login(username='user2', password='password123')

        response = self.client.get(reverse('edit_data_source', args=[self.source.id]))
        self.assertEqual(response.status_code, 404)

    def test_user_cannot_delete_others_source(self):
        """Tests that a user cannot delete another user's source."""
        user2 = User.objects.create_user(username='user2', password='password123')
        Profile.objects.create(user=user2)
        self.client.login(username='user2', password='password123')

        # Try to POST to the delete URL for the first user's source
        response = self.client.post(reverse('delete_data_source', args=[self.source.id]))
        self.assertEqual(response.status_code, 404)

        # Verify the source still exists
        self.assertTrue(AwareDataSource.objects.filter(id=self.source.id).exists())

        

class DataSourceModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.profile = Profile.objects.create(user=self.user)
        self.base = DataSource.objects.create(profile=self.profile, name='Base Source')

    def test_base_methods_raise_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            self.base.get_data_types()
        with self.assertRaises(NotImplementedError):
            self.base.fetch_data()
        with self.assertRaises(NotImplementedError):
            self.base.count_rows()


class AwareDataSourceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.profile = Profile.objects.create(user = self.user)
        self.source = AwareDataSource.objects.create(
            profile=self.profile,
            name='Test Aware Source'
        )

    def test_str_and_display_type(self):
        self.assertEqual(str(self.source), 'Test Aware Source (testuser)')
        self.assertEqual(type(self.source).__name__, 'AwareDataSource')
        self.assertEqual(self.source.display_type, 'AWARE Mobile Data')

    def test_process_returns_no_consent(self):
        result, message = self.source.process()
        self.assertFalse(result)
        self.assertEqual(message, 'No consent found.')

    def test_device_id_conflict_raises_validation_error(self):
        # Create a second user/profile
        user2 = User.objects.create_user(username='other', password='pass')
        profile2 = Profile.objects.create(user=user2)
        # Force a shared device id
        shared_id = uuid.uuid4()
        src1 = AwareDataSource.objects.create(profile=self.profile, name='S1', device_id=shared_id)
        src2 = AwareDataSource(profile=profile2, name='S2', device_id=shared_id)
        with self.assertRaises(ValidationError):
            src2.save()


# Test for JsonUrlDataSource
class JsonUrlDataSourceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.profile = Profile.objects.create(user=self.user)
        self.source = JsonUrlDataSource.objects.create(
            profile=self.profile,
            name='Test JSON Source',
            url='https://example.com/data.json'
        )

    def test_fetch_requires_consent(self):
        result = self.source.fetch_data('raw_json')
        self.assertEqual(result, (False, 'No consent found.'))

    @patch('data_sources.models.jsonurl.requests.get')
    def test_fetch_enriches_and_count(self, mock_get):
        # Create a study and an active consent linking this source
        study = Study.objects.create(title='S', description='d', config_url='http://example.com')
        source_config = StudySourceConfiguration.objects.create(
            study=study, source_type='json_url', status='required'
        )
        Consent.objects.create(
            participant=self.profile, study=study, source_configuration=source_config,
            data_source=self.source, source_type='json_url', is_complete=True, consent_date=timezone.now()
        )

        mock_resp = mock_get.return_value
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = [{'foo': 'bar', 'device_id': 'old-id'}]

        results = self.source.fetch_data('raw_json', limit=10)
        # should be a list with enriched device_id
        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 1)
        row = results[0]
        self.assertEqual(row['device_id'], str(self.source.device_id))
        self.assertEqual(row.get('json_device_id'), 'old-id')

        # count_rows should call requests and return length
        count = self.source.count_rows('raw_json')
        self.assertEqual(count, 1)


# Test for NiimportDataSource
class NiimportDataSourceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.profile = Profile.objects.create(user=self.user)

    @patch('data_sources.models.portability_client.create_donation')
    def test_create_niimport_source_redirects_to_portability(self, mock_create):
        mock_create.return_value = {'id': 1, 'token': 'a1b2c3d4-e5f6-7890-abcd-ef1234567890', 'status': 'pending'}
        self.client.login(username='testuser', password='testpass')
        response = self.client.post(
            reverse('add_data_source', args=['niimport']),
            {
                'name': 'My Niimport Source',
                'niimport_source_type': 'tiktok_portability',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/donate/', response.url)
        mock_create.assert_called_once_with('tiktok_portability')



# Test for DataSource (base class)
class DataSourceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.profile = Profile.objects.create(user=self.user)


# ---------------------------------------------------------------------------
# Portability client unit tests
# ---------------------------------------------------------------------------

@override_settings(
    PORTABILITY_SERVER_URL='http://test-server',
    PORTABILITY_SERVER_TOKEN='test-token',
)
class PortabilityClientTest(TestCase):
    """Unit tests for portability_client — all HTTP calls are patched."""

    EXPECTED_HEADERS = {'Authorization': 'Token test-token'}

    @patch('data_sources.models.portability_client.requests.post')
    def test_create_donation_correct_url_headers_payload(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {'id': 1, 'token': 'abc', 'status': 'pending'}
        mock_post.return_value = mock_response

        result = portability_client.create_donation('niimport_portability')

        mock_post.assert_called_once_with(
            'http://test-server/api/donations/',
            json={'source_type': 'niimport_portability'},
            headers=self.EXPECTED_HEADERS,
            timeout=portability_client.REQUEST_TIMEOUT,
        )
        mock_response.raise_for_status.assert_called_once()
        self.assertEqual(result, {'id': 1, 'token': 'abc', 'status': 'pending'})

    @patch('data_sources.models.portability_client.requests.post')
    def test_create_donation_with_optional_params(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {'id': 2, 'token': 'xyz', 'status': 'pending'}
        mock_post.return_value = mock_response

        result = portability_client.create_donation(
            'tiktok_portability',
            data_start_date='2024-01-01',
            data_end_date='2024-12-31',
            requested_data_types=['activity', 'posts'],
        )

        called_kwargs = mock_post.call_args
        sent_payload = called_kwargs[1]['json']
        self.assertEqual(sent_payload['source_type'], 'tiktok_portability')
        self.assertEqual(sent_payload['data_start_date'], '2024-01-01')
        self.assertEqual(sent_payload['data_end_date'], '2024-12-31')
        self.assertEqual(sent_payload['requested_data_types'], ['activity', 'posts'])
        self.assertEqual(result, {'id': 2, 'token': 'xyz', 'status': 'pending'})

    @patch('data_sources.models.portability_client.requests.get')
    def test_get_donation_correct_url_and_headers(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {'id': 5, 'status': 'processed'}
        mock_get.return_value = mock_response

        result = portability_client.get_donation(5)

        mock_get.assert_called_once_with(
            'http://test-server/api/donations/5/',
            headers=self.EXPECTED_HEADERS,
            timeout=portability_client.REQUEST_TIMEOUT,
        )
        mock_response.raise_for_status.assert_called_once()
        self.assertEqual(result, {'id': 5, 'status': 'processed'})

    @patch('data_sources.models.portability_client.requests.get')
    def test_get_data_correct_url_headers_and_params_forwarded(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {'data': [{'row': 1}], 'count': 1}
        mock_get.return_value = mock_response

        result = portability_client.get_data(
            7,
            data_type='activity',
            start_date='2024-01-01',
            end_date='2024-06-30',
            limit=50,
            offset=10,
        )

        mock_get.assert_called_once_with(
            'http://test-server/api/donations/7/data/',
            params={
                'data_type': 'activity',
                'start_date': '2024-01-01',
                'end_date': '2024-06-30',
                'limit': 50,
                'offset': 10,
            },
            headers=self.EXPECTED_HEADERS,
            timeout=portability_client.REQUEST_TIMEOUT,
        )
        mock_response.raise_for_status.assert_called_once()
        self.assertEqual(result, {'data': [{'row': 1}], 'count': 1})

    @patch('data_sources.models.portability_client.requests.delete')
    def test_delete_donation_correct_url_and_calls_raise_for_status(self, mock_delete):
        mock_response = MagicMock()
        mock_delete.return_value = mock_response

        portability_client.delete_donation(3)

        mock_delete.assert_called_once_with(
            'http://test-server/api/donations/3/',
            headers=self.EXPECTED_HEADERS,
            timeout=portability_client.REQUEST_TIMEOUT,
        )
        mock_response.raise_for_status.assert_called_once()

    @patch('data_sources.models.portability_client.requests.get')
    def test_get_donation_propagates_http_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError('404 Not Found')
        mock_get.return_value = mock_response

        with self.assertRaises(requests.HTTPError):
            portability_client.get_donation(99)

    @patch('data_sources.models.portability_client.requests.post')
    def test_create_donation_propagates_http_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError('500 Server Error')
        mock_post.return_value = mock_response

        with self.assertRaises(requests.HTTPError):
            portability_client.create_donation('niimport_portability')


# ---------------------------------------------------------------------------
# Shared mixin for Niimport portability model tests
# ---------------------------------------------------------------------------

class PortabilityModelTestMixin:
    """Shared tests for niimport/TikTok portability data sources."""

    model_class = None  # set in subclass

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.profile = Profile.objects.create(user=self.user)

    def _make_source(self, **kwargs):
        defaults = {'profile': self.profile, 'name': 'test'}
        defaults.update(kwargs)
        return self.model_class.objects.create(**defaults)

    # -- get_setup_url -------------------------------------------------------

    @override_settings(PORTABILITY_SERVER_URL='http://portability')
    def test_get_setup_url_with_existing_donation_returns_url(self):
        token = uuid.uuid4()
        source = self._make_source(donation_id=1, donation_token=token)
        expected = f'http://portability/donate/{token}/'
        self.assertEqual(source.get_setup_url(), expected)

    @override_settings(PORTABILITY_SERVER_URL='http://portability')
    @patch('data_sources.models.portability_client.create_donation')
    def test_get_setup_url_creates_donation_if_missing(self, mock_create):
        token = uuid.uuid4()
        mock_create.return_value = {'id': 1, 'token': str(token)}
        source = self._make_source(
            data_start=date(2024, 1, 1),
            data_end=date(2024, 6, 30),
            configuration={'requested_data_types': 'activity,posts'},
        )
        url = source.get_setup_url()
        mock_create.assert_called_once_with(
            source.NIIMPORT_SOURCE_TYPE,
            data_start_date=date(2024, 1, 1),
            data_end_date=date(2024, 6, 30),
            requested_data_types='activity,posts',
        )
        self.assertEqual(url, f'http://portability/donate/{token}/')

    # -- get_data_types ------------------------------------------------------

    @patch('data_sources.models.portability_client.get_data')
    def test_get_data_types_returns_list(self, mock_get_data):
        mock_get_data.return_value = {'data_types': ['activity', 'posts']}
        source = self._make_source(donation_id=42)

        result = source.get_data_types()

        mock_get_data.assert_called_once_with(42)
        self.assertEqual(result, ['activity', 'posts'])

    def test_get_data_types_without_donation_id_returns_empty(self):
        source = self._make_source(donation_id=None)
        self.assertEqual(source.get_data_types(), [])

    @patch('data_sources.models.portability_client.get_data', side_effect=Exception('network error'))
    def test_get_data_types_exception_returns_empty(self, _mock):
        source = self._make_source(donation_id=42)
        with self.assertLogs('data_sources.models.niimport', level='WARNING'):
            self.assertEqual(source.get_data_types(), [])

    # -- fetch_data ----------------------------------------------------------

    @patch('data_sources.models.portability_client.get_data')
    def test_fetch_data_returns_data_list_with_correct_params(self, mock_get_data):
        rows = [{'row': 1}, {'row': 2}]
        mock_get_data.return_value = {'data': rows}
        source = self._make_source(donation_id=7)

        result = source.fetch_data('activity', limit=25, start_date='2024-01-01',
                                   end_date='2024-06-30', offset=5)

        mock_get_data.assert_called_once_with(
            7,
            data_type='activity',
            start_date='2024-01-01',
            end_date='2024-06-30',
            limit=25,
            offset=5,
        )
        self.assertEqual(result, rows)

    def test_fetch_data_without_donation_id_returns_empty(self):
        source = self._make_source(donation_id=None)
        self.assertEqual(source.fetch_data('activity'), [])

    @patch('data_sources.models.portability_client.get_data', side_effect=Exception('boom'))
    def test_fetch_data_exception_returns_empty(self, _mock):
        source = self._make_source(donation_id=7)
        with self.assertLogs('data_sources.models.niimport', level='WARNING'):
            self.assertEqual(source.fetch_data('activity'), [])

    # -- count_rows ----------------------------------------------------------

    @patch('data_sources.models.portability_client.get_data')
    def test_count_rows_returns_count(self, mock_get_data):
        mock_get_data.return_value = {'count': 99}
        source = self._make_source(donation_id=7)

        result = source.count_rows('activity')

        self.assertEqual(result, 99)

    def test_count_rows_without_donation_id_returns_zero(self):
        source = self._make_source(donation_id=None)
        self.assertEqual(source.count_rows('activity'), 0)

    @patch('data_sources.models.portability_client.get_data', side_effect=Exception('boom'))
    def test_count_rows_exception_returns_zero(self, _mock):
        source = self._make_source(donation_id=7)
        with self.assertLogs('data_sources.models.niimport', level='WARNING'):
            self.assertEqual(source.count_rows('activity'), 0)

    # -- revoke_before_delete ------------------------------------------------

    @patch('data_sources.models.portability_client.delete_donation')
    def test_revoke_before_delete_calls_delete_donation(self, mock_delete):
        source = self._make_source(donation_id=5)
        source.revoke_before_delete()
        mock_delete.assert_called_once_with(5)

    @patch('data_sources.models.portability_client.delete_donation')
    def test_revoke_before_delete_without_donation_id_skips_call(self, mock_delete):
        source = self._make_source(donation_id=None)
        source.revoke_before_delete()
        mock_delete.assert_not_called()

    @patch('data_sources.models.portability_client.delete_donation', side_effect=Exception('server down'))
    def test_revoke_before_delete_swallows_exception(self, _mock):
        source = self._make_source(donation_id=5)
        # Should not raise; the error is logged (captured here so it doesn't pollute output).
        with self.assertLogs('data_sources.models.niimport', level='WARNING'):
            source.revoke_before_delete()

    # -- _process_data -------------------------------------------------------

    @patch('data_sources.models.portability_client.get_donation')
    def test_process_data_processed_sets_active_status(self, mock_get_donation):
        mock_get_donation.return_value = {'status': 'processed'}
        source = self._make_source(donation_id=10)

        source._process_data()

        source.refresh_from_db()
        self.assertEqual(source.processing_status, 'processed')
        self.assertEqual(source.status, 'active')

    @patch('data_sources.models.portability_client.get_donation')
    def test_process_data_error_sets_error_processing_status(self, mock_get_donation):
        mock_get_donation.return_value = {'status': 'error'}
        source = self._make_source(donation_id=10)

        source._process_data()

        source.refresh_from_db()
        self.assertEqual(source.processing_status, 'error')

    @patch('data_sources.models.portability_client.get_donation')
    def test_process_data_authorized_sets_authorized_processing_status(self, mock_get_donation):
        mock_get_donation.return_value = {'status': 'authorized'}
        source = self._make_source(donation_id=10)

        source._process_data()

        source.refresh_from_db()
        self.assertEqual(source.processing_status, 'authorized')

    @patch('data_sources.models.portability_client.get_donation')
    def test_process_data_processing_sets_processing_status(self, mock_get_donation):
        mock_get_donation.return_value = {'status': 'processing'}
        source = self._make_source(donation_id=10)

        source._process_data()

        source.refresh_from_db()
        self.assertEqual(source.processing_status, 'processing')

    @patch('data_sources.models.portability_client.get_donation')
    def test_process_data_without_donation_id_returns_without_api_call(self, mock_get_donation):
        source = self._make_source(donation_id=None)
        source._process_data()
        mock_get_donation.assert_not_called()

    @patch('data_sources.models.portability_client.get_donation', side_effect=Exception('timeout'))
    def test_process_data_swallows_exception(self, _mock):
        source = self._make_source(donation_id=10)
        # Should not raise; the error is logged (captured here so it doesn't pollute output).
        with self.assertLogs('data_sources.models.niimport', level='WARNING'):
            source._process_data()


# ---------------------------------------------------------------------------
# Concrete model test classes using the mixin
# ---------------------------------------------------------------------------

class NiimportPortabilityModelTest(PortabilityModelTestMixin, TestCase):
    model_class = NiimportDataSource


# ---------------------------------------------------------------------------
# View tests: creation failure rollback and delete with revoke
# ---------------------------------------------------------------------------

class PortabilityViewCreationRollbackTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.profile = Profile.objects.create(user=self.user)
        self.client.login(username='testuser', password='testpass')

    @patch('data_sources.models.portability_client.create_donation',
           side_effect=Exception('portability server unreachable'))
    def test_niimport_creation_failure_redirects_to_dashboard(self, _mock):
        response = self.client.post(
            reverse('add_data_source', args=['niimport']),
            {
                'name': 'Failing Niimport Source',
                'niimport_source_type': 'google_portability',
            },
        )
        self.assertRedirects(response, reverse('dashboard'))

    @patch('data_sources.models.portability_client.create_donation',
           side_effect=Exception('portability server unreachable'))
    def test_niimport_creation_failure_rolls_back_source(self, _mock):
        self.client.post(
            reverse('add_data_source', args=['niimport']),
            {
                'name': 'Failing Niimport Source',
                'niimport_source_type': 'google_portability',
            },
        )
        self.assertFalse(
            NiimportDataSource.objects.filter(profile=self.profile).exists()
        )

    @patch('data_sources.models.portability_client.create_donation',
           side_effect=Exception('portability server unreachable'))
    def test_tiktok_creation_failure_redirects_to_dashboard(self, _mock):
        response = self.client.post(
            reverse('add_data_source', args=['niimport']),
            {
                'name': 'Failing TikTok Source',
                'niimport_source_type': 'tiktok_portability',
            },
        )
        self.assertRedirects(response, reverse('dashboard'))


class PortabilityViewDeleteTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.profile = Profile.objects.create(user=self.user)
        self.client.login(username='testuser', password='testpass')

    @patch('data_sources.models.portability_client.delete_donation')
    def test_delete_niimport_source_calls_delete_donation(self, mock_delete):
        source = NiimportDataSource.objects.create(
            profile=self.profile,
            name='My niimport Source',
            donation_id=42,
        )
        self.client.post(reverse('delete_data_source', args=[source.id]))
        mock_delete.assert_called_once_with(42)

    @patch('data_sources.models.portability_client.delete_donation')
    def test_delete_niimport_source_removes_from_db(self, _mock):
        source = NiimportDataSource.objects.create(
            profile=self.profile,
            name='My niimport Source',
            donation_id=42,
        )
        self.client.post(reverse('delete_data_source', args=[source.id]))
        self.assertFalse(
            NiimportDataSource.objects.filter(id=source.id).exists()
        )


# ---------------------------------------------------------------------------
# New tests added for consent-snapshot + bug-fix plan
# ---------------------------------------------------------------------------

class NiimportDisplayTypeForConfigurationTest(TestCase):
    """Fix B: classmethod display_type_for_configuration maps config correctly."""

    def test_google_portability(self):
        self.assertEqual(
            NiimportDataSource.display_type_for_configuration({'niimport_source_type': 'google_portability'}),
            'Google Portability Data',
        )

    def test_tiktok_portability(self):
        self.assertEqual(
            NiimportDataSource.display_type_for_configuration({'niimport_source_type': 'tiktok_portability'}),
            'TikTok Portability Data',
        )

    def test_tiktok_export(self):
        self.assertEqual(
            NiimportDataSource.display_type_for_configuration({'niimport_source_type': 'tiktok_export'}),
            'TikTok Export Data',
        )

    def test_unknown_returns_fallback(self):
        self.assertEqual(
            NiimportDataSource.display_type_for_configuration({'niimport_source_type': 'unknown'}),
            'Missing portability data type',
        )

    def test_empty_config_returns_fallback(self):
        self.assertEqual(
            NiimportDataSource.display_type_for_configuration({}),
            'Missing portability data type',
        )


class AddDataSourceCopiesConsentParamsTest(TestCase):
    """
    Fix 1e: When creating a new DataSource with a consent_id, the new source
    should copy data_start/data_end/configuration from the consent snapshot
    rather than from the StudySourceConfiguration.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='consent_copy_user', password='testpass')
        self.profile = Profile.objects.create(user=self.user)
        self.client.login(username='consent_copy_user', password='testpass')
        self.study = Study.objects.create(title='Consent Copy Study', config_url='http://example.com')
        # Config has one set of dates
        self.source_config = StudySourceConfiguration.objects.create(
            study=self.study,
            source_type='json_url',
            status='required',
            data_start=timezone.make_aware(datetime(2024, 1, 1)),
            data_end=timezone.make_aware(datetime(2024, 12, 31)),
        )
        # Consent snapshot has a DIFFERENT (narrower) window
        self.consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=self.source_config,
            source_type='json_url',
            consent_text_accepted=True,
            data_start=timezone.make_aware(datetime(2024, 3, 1)),
            data_end=timezone.make_aware(datetime(2024, 9, 30)),
            configuration={},
        )

    def test_new_source_copies_params_from_consent(self):
        url = reverse('add_data_source', args=['json_url'])
        response = self.client.post(
            f"{url}?consent_id={self.consent.id}&source_configuration_id={self.source_config.id}",
            {
                'name': 'My JSON Source',
                'url': 'https://example.com/data.json',
            },
        )
        source = JsonUrlDataSource.objects.filter(profile=self.profile).latest('id')
        # Must use consent's dates, not config's
        self.assertEqual(source.data_start, date(2024, 3, 1))
        self.assertEqual(source.data_end, date(2024, 9, 30))


class AwareConfigEndpointTest(TestCase):
    """Fix A: config token endpoint must handle missing source config gracefully."""

    def setUp(self):
        self.user = User.objects.create_user(username='aware_cfg_user', password='testpass')
        self.profile = Profile.objects.create(user=self.user)
        self.study = Study.objects.create(
            title='Aware Config Study', description='desc',
            config_url='http://example.com',
            contact_name='Jane Doe',
            contact_email='jane@example.com',
        )
        self.source_config = StudySourceConfiguration.objects.create(
            study=self.study,
            source_type='aware',
            status='required',
            configuration={
                'questions': [{'id': 'q1', 'text': 'How are you?'}],
                'schedules': [{'id': 's1', 'name': 'Daily'}],
                'sensors': [{'setting': 'accelerometer', 'value': True}],
            },
        )
        self.aware_source = AwareDataSource.objects.create(
            profile=self.profile,
            name='Aware Config Test Source',
            status='active',
        )
        self.consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=self.source_config,
            source_type='aware',
            data_source=self.aware_source,
            is_complete=True,
            consent_date=timezone.now(),
            # The AWARE config endpoint now reads the consent's own snapshot
            # (what accept_configuration copies at consent time), not the live config.
            configuration=self.source_config.configuration,
        )

    def test_config_endpoint_returns_200_and_merged_json(self):
        url = reverse('datasource_token_view', kwargs={
            'token': self.aware_source.config_token,
            'view_type': 'config',
        })
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('questions', data)
        self.assertIn('schedules', data)
        self.assertIn('sensors', data)
        # The study-specific questions should be merged in
        self.assertTrue(any(q.get('id') == 'q1' for q in data['questions']))

    def test_config_endpoint_no_aware_config_does_not_crash(self):
        """If there is no StudySourceConfiguration with source_type='aware', the endpoint
        should still return 200 without raising AttributeError."""
        self.source_config.delete()
        url = reverse('datasource_token_view', kwargs={
            'token': self.aware_source.config_token,
            'view_type': 'config',
        })
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
