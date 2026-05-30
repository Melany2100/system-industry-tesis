from django.shortcuts import render
from .models import Informe

def lista_informes(request):
    informes = Informe.objects.order_by('-fecha')
    return render(request, 'informes/index.html', {'informes': informes})
