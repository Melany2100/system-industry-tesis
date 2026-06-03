from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from .models import Informe
from core_apps.common.permissions import is_admin_user

@login_required(login_url="/login/")
def lista_informes(request):
    if not is_admin_user(request.user):
        return HttpResponseForbidden("Solo un administrador puede ver informes.")

    informes = Informe.objects.order_by('-fecha')
    critical_count = informes.filter(epp_correcto=False).count()
    return render(request, 'informes/index.html', {
        'informes': informes,
        'critical_count': critical_count,
        'segment': 'lista_informes',
    })
