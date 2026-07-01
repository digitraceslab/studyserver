from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('terms/', views.terms_of_service, name='terms_of_service'),
    path('privacy/', views.privacy_statement, name='privacy_statement'),
    path('terms/version/<int:pk>/', views.terms_of_service_version, name='terms_of_service_version'),
    path('privacy/version/<int:pk>/', views.privacy_statement_version, name='privacy_statement_version'),
    path('signup/', views.signup, name='signup'),
    path('signup/researcher/', views.signup_researcher, name='signup_researcher'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('token/', views.manage_token, name='manage_token'),
    path('researcher-dashboard/', views.researcher_dashboard, name='researcher_dashboard'),
    path('participant/<int:study_id>/<int:participant_id>/', views.participant_detail, name='participant_detail'),
    path('api/data/', views.my_data_api, name='my_data_api'),
    path('dashboard/protected-identifiers/', views.update_protected_identifiers, name='update_protected_identifiers'),
]
