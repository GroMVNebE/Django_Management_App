from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.http import Http404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_POST
from django.core.exceptions import ValidationError
from django.db.models import Sum, F, FloatField, ExpressionWrapper, Value
from django.db.models.functions import Coalesce
from .models import Object, Product, ParsingBlacklist, ObjectStatus, ProductItem
from .utils import parse_spec, decode_id


def is_master(user):
    return user.groups.filter(name='master').exists()


def is_worker(user):
    return user.groups.filter(name='worker').exists()


@login_required
def index(request):
    if is_master(request.user):
        return redirect('master_dashboard')
    elif is_worker(request.user):
        return redirect('worker')
    else:
        return redirect('login')


@login_required
@user_passes_test(is_master, login_url='')
def master_dashboard(request):
    """Главная страница для мастера со списком всех объектов"""
    objects_list = Object.objects.annotate(
        total_payment=Coalesce(
            Sum(
                ExpressionWrapper(
                    F('products__payment') * F('products__quantity'),
                    output_field=FloatField()
                )
            ),
            Value(0.0)
        )
    ).all()

    for obj in objects_list:
        completed_items = ProductItem.objects.filter(
            product__object=obj,
            status=ProductItem.StatusChoices.COMPLETED
        ).select_related('product')

        completed_payment = sum(
            item.quantity * item.product.payment
            for item in completed_items
            if item.product.quantity > 0
        )

        if obj.total_payment > 0:
            obj.progress_percentage = min(
                int((float(completed_payment) / float(obj.total_payment)) * 100),
                100
            )
        else:
            obj.progress_percentage = 0

    context = {
        'objects': objects_list,
    }
    return render(request, 'master_dashboard.html', context)


@login_required
@user_passes_test(is_master, login_url='')
def import_objects_view(request):
    """Страница импорта объектов из Excel."""
    if request.method == 'POST':
        excel_file = request.FILES.get('excel_file')

        if not excel_file:
            messages.error(request, 'Пожалуйста, выберите файл для загрузки.')
            return render(request, 'import_objects.html')
        if not excel_file.name.endswith(('.xlsx', '.xls', '.xlsm')):
            messages.error(
                request, 'Неверный формат файла. Пожалуйста, загрузите файл Excel (.xlsx, .xlsm или .xls).')
            return render(request, 'import_objects.html')

        object_number = excel_file.name.split()[0]
        blacklist = [p.value for p in ParsingBlacklist.objects.all()]
        try:
            products = parse_spec(excel_file, blacklist)
        except ValidationError as e:
            messages.error(
                request, f'При парсинге файла произошла ошибка: {e}')
            return render(request, 'import_objects.html')

        object = Object.objects.create(number=object_number)
        in_queue_status = ObjectStatus.objects.get(title="В очереди")
        object.status.add(in_queue_status)
        product_number = '1'
        number_len = len(str(len(products)))
        for product in products:
            product.number = object_number + '-' + '0' * \
                (number_len - len(product_number)) + product_number
            if product.labor_cost == 0:
                continue
            if product.divIntoParts is False or len(product.parts) == 0:
                Product.objects.create(
                    object=object, product_number=product.number, title=product.name, quantity=1, payment=product.payment)
            else:
                for part in product.parts:
                    Product.objects.create(
                        object=object, product_number=product.number, title=product.name, part_name=part.name, quantity=1, payment=part.payment)
            product_number = str(int(product_number)+1)
        context = dict()
        context['products'] = Product.objects.filter(object=object)
        context['object'] = object

        messages.success(request, 'Импорт данных успешно завершён!')
        return render(request, 'import_objects.html', context)

    return render(request, 'import_objects.html')


@login_required
@user_passes_test(is_master, login_url='')
@require_POST
def toggle_object_status_view(request, object_id):
    """Переключение статуса объекта между 'В работе' и 'В очереди'"""
    obj = get_object_or_404(Object, pk=object_id)

    in_work_status = ObjectStatus.objects.get(title="В работе")
    in_queue_status = ObjectStatus.objects.get(title="В очереди")

    if in_work_status in obj.status.all():
        obj.status.remove(in_work_status)
        obj.status.add(in_queue_status)
        messages.info(request, f'Объект № {obj} переведен в очередь')
    else:
        obj.status.remove(in_queue_status)
        obj.status.add(in_work_status)
        messages.success(request, f'Объект № {obj} введен в работу')

    return redirect('object_detail', hashed_id=obj.hashid)


@login_required
@user_passes_test(is_master, login_url='')
@require_POST
def delete_object_view(request, object_id):
    """Удаление объекта (только если нет экземпляров ProductItem)"""
    obj = get_object_or_404(Object, pk=object_id)

    has_items = ProductItem.objects.filter(product__object=obj).exists()

    if has_items:
        messages.error(
            request,
            'Нельзя удалить объект, у которого уже созданы экземпляры изделий (ProductItem)'
        )
        return redirect('object_detail', hashed_id=obj.hashid)

    obj_number = obj.number
    obj.delete()
    messages.success(request, f'Объект № {obj_number} успешно удален')
    return redirect('master_dashboard')


@login_required
@user_passes_test(is_master, login_url='')
def object_detail_view(request, hashed_id):
    """Страница деталей объекта со списком изделий и их экземпляров."""
    object_id = decode_id(hashed_id)
    if object_id is None:
        raise Http404("Объект не найден")
    obj = get_object_or_404(Object, pk=object_id)

    products = Product.objects.filter(object=obj).prefetch_related(
        'items__employee'
    )

    has_product_items = ProductItem.objects.filter(
        product__object=obj).exists()

    is_in_work = obj.status.filter(title="В работе").exists()

    context = {
        'object': obj,
        'products': products,
        'has_product_items': has_product_items,
        'is_in_work': is_in_work,
    }
    return render(request, 'object_detail.html', context)


def login_view(request):
    """Представление для входа пользователей"""
    if request.user.is_authenticated:
        return redirect('master_dashboard')

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            next_url = request.GET.get('next', 'master_dashboard')
            return redirect(next_url)
        else:
            messages.error(request, 'Неверное имя пользователя или пароль.')

    return render(request, 'login.html')


def logout_view(request):
    """Выход из системы"""
    logout(request)
    return redirect('login')
