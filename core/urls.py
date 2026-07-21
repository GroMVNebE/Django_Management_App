from django.urls import path
from . import views

urlpatterns = [
    path('', views.master_dashboard, name='master_dashboard'),
]
