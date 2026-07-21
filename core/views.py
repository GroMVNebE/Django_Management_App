from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from .models import Object


def is_master(user):
    return user.groups.filter(name='master').exists()


@login_required
@user_passes_test(is_master, login_url='/access-denied/')
def master_dashboard(request):
    """Главная страница для мастера со списком всех объектов"""
    objects_list = Object.objects.select_related('status', 'client').all()

    context = {
        'objects': objects_list,
    }
    return render(request, 'master_dashboard.html', context)
