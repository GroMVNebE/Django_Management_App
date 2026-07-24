from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.http import Http404
from django.utils import timezone
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_POST
from django.core.exceptions import ValidationError
from django.db.models import Sum, F, FloatField, ExpressionWrapper, Value, DecimalField
from django.db.models.functions import Coalesce
from decimal import Decimal
from .models import Object, Product, ParsingBlacklist, ObjectStatus, ProductItem, Employee
from .utils import parse_spec, decode_id


def is_master(user):
    return user.groups.filter(name='master').exists()


def is_worker(user):
    return user.groups.filter(name='worker').exists()


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
            next_url = request.GET.get('next', 'index')
            return redirect(next_url)
        else:
            messages.error(request, 'Неверное имя пользователя или пароль.')

    return render(request, 'login.html')


def logout_view(request):
    """Выход из системы"""
    logout(request)
    return redirect('login')


@login_required
def index(request):
    if is_master(request.user):
        return redirect('master_dashboard')
    elif is_worker(request.user):
        return redirect('employee_dashboard')
    else:
        return redirect('logout')


@login_required
@user_passes_test(is_master, login_url='/')
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
@user_passes_test(is_master, login_url='/')
def items_in_work(request):
    """Страница с изделиями в работе и в очереди"""

    in_progress_items = ProductItem.objects.filter(
        status=ProductItem.StatusChoices.IN_PROGRESS
    ).select_related(
        'product', 'product__object', 'employee'
    ).order_by('start_time')

    queued_items = ProductItem.objects.filter(
        status=ProductItem.StatusChoices.QUEUED
    ).select_related(
        'product', 'product__object', 'employee'
    ).order_by('id')

    context = {
        'in_progress_items': in_progress_items,
        'queued_items': queued_items,
    }
    return render(request, 'items_in_work.html', context)


@login_required
@user_passes_test(is_master, login_url='/')
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
@user_passes_test(is_master, login_url='/')
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
@user_passes_test(is_master, login_url='/')
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
@user_passes_test(is_master, login_url='/')
@require_POST
def assign_worker_view(request, product_id):
    """Назначить работника на изготовление изделия (добавить в очередь)"""
    product = get_object_or_404(Product, pk=product_id)

    employee_id = request.POST.get('employee_id')
    employee = get_object_or_404(Employee, pk=employee_id)

    try:
        quantity = Decimal(request.POST.get('quantity', '1.0'))
    except (ValueError, TypeError):
        messages.error(request, 'Указано некорректное количество')
        return redirect('object_detail', hashed_id=product.object.hashid)

    if quantity <= 0:
        messages.error(request, 'Количество должно быть больше 0')
        return redirect('object_detail', hashed_id=product.object.hashid)

    available_qty = product.available_quantity
    if quantity > available_qty:
        messages.error(
            request,
            f'Указанное количество ({quantity}) превышает доступный остаток ({available_qty})'
        )
        return redirect('object_detail', hashed_id=product.object.hashid)

    existing_queued_item = ProductItem.objects.filter(
        product=product,
        employee=employee,
        status=ProductItem.StatusChoices.QUEUED
    ).first()

    if existing_queued_item:
        existing_queued_item.quantity += quantity
        existing_queued_item.save()
        messages.success(
            request, f'Изделие "{product}" успешно добавлено в очередь для рабочего {employee}')
    else:
        ProductItem.objects.create(
            product=product,
            employee=employee,
            quantity=quantity,
            status=ProductItem.StatusChoices.QUEUED
        )
        messages.success(
            request, f'Изделие "{product}" успешно добавлено в очередь для рабочего {employee}')

    return redirect('object_detail', hashed_id=product.object.hashid)


@login_required
@require_POST
def complete_product_item(request, item_id):
    """Отметить экземпляр изделия как завершённый"""
    item = get_object_or_404(ProductItem, pk=item_id)
    redirect_hashid = item.product.object.hashid

    is_master_user = is_master(request.user)
    is_assigned_worker = item.employee and item.employee.user == request.user

    if not (is_master_user or is_assigned_worker):
        return redirect('logout')

    if item.status != ProductItem.StatusChoices.COMPLETED:
        existing_completed_item = ProductItem.objects.filter(
            product=item.product,
            employee=item.employee,
            status=ProductItem.StatusChoices.COMPLETED
        ).first()

        if existing_completed_item:
            existing_completed_item.quantity += item.quantity
            existing_completed_item.end_time = timezone.now()
            existing_completed_item.save()
            item.delete()
        else:
            item.status = ProductItem.StatusChoices.COMPLETED
            item.end_time = timezone.now()
            item.save()
        if is_master_user:
            messages.success(
                request, f'Изделие "{item.product}", выполняемое {item.employee}, отмечено, как завершённое')
        else:
            messages.success(
                request, f'Работа над изделием "{item.product}" закончена')
    else:
        messages.info(request, 'Этот экземпляр уже завершён')

    if is_master_user:
        return redirect('object_detail', hashed_id=redirect_hashid)
    else:
        return redirect('employee_items')


@login_required
@user_passes_test(is_master, login_url='/')
def object_detail_view(request, hashed_id):
    """Страница деталей объекта со списком изделий и их экземпляров"""
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

    employees = Employee.objects.all()

    context = {
        'object': obj,
        'products': products,
        'has_product_items': has_product_items,
        'is_in_work': is_in_work,
        'employees': employees,
    }
    return render(request, 'object_detail.html', context)


@login_required
@user_passes_test(is_worker, login_url='/')
@require_POST
def start_queued_item(request, item_id):
    """Взять в работу экземпляр изделия из очереди"""
    item = get_object_or_404(
        ProductItem,
        pk=item_id,
        status=ProductItem.StatusChoices.QUEUED,
        employee__user=request.user
    )

    existing_in_progress_item = ProductItem.objects.filter(
        product=item.product,
        employee=item.employee,
        status=ProductItem.StatusChoices.IN_PROGRESS
    ).first()

    if existing_in_progress_item:
        existing_in_progress_item.quantity += item.quantity
        existing_in_progress_item.save()
        item.delete()
    else:
        item.status = ProductItem.StatusChoices.IN_PROGRESS
        item.start_time = timezone.now()
        item.save()

    messages.success(request, f'Изделие "{item.product}" взято в работу')
    return redirect('employee_dashboard')


@login_required
@user_passes_test(is_worker, login_url='/')
@require_POST
def start_product_item(request, product_id):
    """Создать новый экземпляр изделия и взять его в работу"""
    product = get_object_or_404(Product, pk=product_id)

    employee = getattr(request.user, 'employee_profile', None)
    if not employee:
        messages.error(request, 'Профиль работника не найден')
        return redirect('employee_dashboard')

    try:
        quantity = Decimal(request.POST.get('quantity', '1.0'))
    except (ValueError, TypeError):
        messages.error(request, 'Указано некорректное количество')
        return redirect('employee_dashboard')

    available_qty = product.available_quantity
    if quantity <= 0:
        messages.error(request, 'Количество должно быть больше 0')
        return redirect('employee_dashboard')

    if quantity > available_qty:
        messages.error(
            request, f'Указанное количество ({quantity}) превышает доступный остаток ({available_qty})')
        return redirect('employee_dashboard')

    existing_in_progress_item = ProductItem.objects.filter(
        product=product,
        employee=employee,
        status=ProductItem.StatusChoices.IN_PROGRESS
    ).first()

    if existing_in_progress_item:
        existing_in_progress_item.quantity += quantity
        existing_in_progress_item.save()
    else:
        ProductItem.objects.create(
            product=product,
            quantity=quantity,
            status=ProductItem.StatusChoices.IN_PROGRESS,
            start_time=timezone.now(),
            employee=employee
        )

    messages.success(
        request, f'Изделие "{product}" взято в работу ({quantity} шт.)')
    return redirect('employee_dashboard')


@login_required
@user_passes_test(is_worker, login_url='/')
def employee_dashboard(request):
    """Страница рабочего: список изделий в работе и в очереди"""

    in_work_objects = Object.objects.filter(status__title="В работе")

    available_products = Product.objects.filter(
        object__in=in_work_objects
    ).annotate(
        used_quantity=Coalesce(
            Sum('items__quantity'),
            Value(Decimal('0.0')),
            output_field=DecimalField()
        )
    ).annotate(
        calculated_available_quantity=ExpressionWrapper(
            F('quantity') - F('used_quantity'),
            output_field=DecimalField()
        )
    ).filter(
        calculated_available_quantity__gt=0
    ).select_related('object').order_by('product_number')

    queued_items = ProductItem.objects.filter(
        status=ProductItem.StatusChoices.QUEUED,
        employee__user=request.user
    ).select_related('product', 'product__object', 'employee')

    context = {
        'available_products': available_products,
        'queued_items': queued_items,
    }
    return render(request, 'employee_dashboard.html', context)


@login_required
@user_passes_test(is_worker, login_url='/')
@require_POST
def cancel_product_item(request, item_id):
    """Отмена изготовления экземпляра изделия"""
    item = get_object_or_404(ProductItem, pk=item_id)

    product_title = str(item.product)
    quantity = item.quantity

    item.delete()

    messages.success(
        request, f'Изготовление изделия "{product_title}" ({quantity} шт.) отменено')

    return redirect('employee_items')


@login_required
@user_passes_test(is_worker, login_url='/')
def employee_items(request):
    """Страница работника со всеми его изделиями в работе"""
    employee = Employee.objects.filter(user=request.user).first()

    in_progress_items = ProductItem.objects.filter(
        employee=employee,
        status=ProductItem.StatusChoices.IN_PROGRESS
    ).select_related('product', 'product__object').order_by('start_time')

    context = {
        'employee': employee,
        'in_progress_items': in_progress_items,
    }
    return render(request, 'employee_items.html', context)
