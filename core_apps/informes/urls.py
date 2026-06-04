from django.urls import path
from . import views

urlpatterns = [
    path('', views.lista_informes, name='lista_informes'),
    path('exportar/<str:periodo>/<str:formato>/', views.exportar_informes, name='exportar_informes'),
]
