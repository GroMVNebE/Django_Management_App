from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.contrib import messages
from django.http import Http404
from django.utils import timezone
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User, Group
from django.views.decorators.http import require_POST
from django.core.exceptions import ValidationError
from django.db.models import Sum, F, Q, FloatField, ExpressionWrapper, Value, DecimalField
from django.db.models.functions import Coalesce, Concat
from decimal import Decimal
from .models import Object, Product, ParsingBlacklist, ObjectStatus, ProductItem, Employee, Client, ContactPerson
from .utils import parse_spec, decode_id, encode_id


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
@require_POST
def update_object_status_view(request, object_id):
    """Изменение статуса объекта"""
    obj = get_object_or_404(Object, pk=object_id)
    status_id = request.POST.get('status_id')

    if status_id:
        new_status = get_object_or_404(ObjectStatus, pk=status_id)
        obj.status = new_status
        messages.success(
            request, f'Статус объекта № {obj.number} изменён на "{new_status.title}"')
    else:
        obj.status = None
        messages.info(request, f'Статус объекта № {obj.number} сброшен')

    obj.save()
    return redirect('master_dashboard')


@login_required
@user_passes_test(is_master, login_url='/')
def master_dashboard(request):
    """Главная страница для мастера со списком всех объектов"""
    query = request.GET.get('q', '').strip()
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
    ).filter(is_hidden=False).select_related('client', 'status').order_by('-id')
    if query:
        objects_list = objects_list.filter(
            Q(number__icontains=query) |
            Q(client__title__icontains=query) |
            Q(title__icontains=query)
        ).distinct()

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
        'search_query': query,
        'all_statuses': ObjectStatus.objects.all(),
    }
    if request.headers.get('HX-Request'):
        return render(request, 'includes/objects_table_partial.html', context)
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
    if request.headers.get('HX-Request'):
        return render(request, 'includes/items_in_work_partial.html', context)
    return render(request, 'items_in_work.html', context)


@login_required
@user_passes_test(is_master, login_url='/')
def import_objects_view(request):
    """Страница импорта объектов из Excel."""
    if request.method == 'POST':
        excel_file = request.FILES.get('excel_file')
        object_id = request.POST.get('object_id')

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

        if object_id:
            object = get_object_or_404(Object, pk=object_id)
            if object.number != object_number:
                messages.error(
                    request, f'Номер текущего объекта ({object.number}) не совпадает с номером в спецификации ({object_number})')
                return redirect('object_detail', hashed_id=object.hashid)
        else:
            object = Object.objects.create(number=object_number)
            in_queue_status = ObjectStatus.objects.get(title="В очереди")
            object.status = in_queue_status
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
        if object_id:
            return redirect('object_detail', hashed_id=object.hashid)
        context = dict()
        context['products'] = Product.objects.filter(object=object)
        context['object'] = object

        messages.success(request, 'Импорт данных успешно завершён!')
        return render(request, 'import_objects.html', context)

    return render(request, 'import_objects.html')


@login_required
@user_passes_test(is_master, login_url='/')
def parsing_blacklist(request):
    """Страница управления чёрным списком масок для импорта спецификаций"""

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add':
            value = request.POST.get('value', '').strip()
            if value:
                if ParsingBlacklist.objects.filter(value__iexact=value).exists():
                    messages.warning(
                        request, f'Маска "{value}" уже существует в чёрном списке.')
                else:
                    ParsingBlacklist.objects.create(value=value)
                    messages.success(
                        request, f'Маска "{value}" успешно добавлена в чёрный список.')
            else:
                messages.error(request, 'Значение маски не может быть пустым.')

        elif action == 'delete':
            item_id = request.POST.get('item_id')
            blacklist_item = get_object_or_404(ParsingBlacklist, pk=item_id)
            value_name = blacklist_item.value
            blacklist_item.delete()
            messages.success(
                request, f'Маска "{value_name}" удалена из чёрного списка.')

        return redirect('parsing_blacklist')

    blacklist_items = ParsingBlacklist.objects.all().order_by('value')

    context = {
        'blacklist_items': blacklist_items,
    }
    return render(request, 'parsing_blacklist.html', context)


@login_required
@user_passes_test(is_master, login_url='/')
@require_POST
def toggle_object_status_view(request, object_id):
    """Переключение статуса объекта между 'В работе' и 'В очереди'"""
    obj = get_object_or_404(Object, pk=object_id)

    in_work_status = ObjectStatus.objects.get(title="В работе")
    in_queue_status = ObjectStatus.objects.get(title="В очереди")

    if in_work_status == obj.status:
        obj.status = in_queue_status
        messages.info(request, f'Объект № {obj} переведен в очередь')
    else:
        obj.status = in_work_status
        messages.success(request, f'Объект № {obj} введен в работу')
    obj.save()

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

    referer_url = request.META.get('HTTP_REFERER')
    if referer_url:
        return redirect(referer_url)

    if is_master_user:
        return redirect('object_detail', hashed_id=redirect_hashid)
    else:
        return redirect('employee_items')


@login_required
@user_passes_test(is_master, login_url='/')
@require_POST
def toggle_object_hidden(request, object_id):
    """Скрытие или восстановление объекта"""
    obj = get_object_or_404(Object, pk=object_id)
    obj.is_hidden = not obj.is_hidden
    obj.save()

    if obj.is_hidden:
        messages.success(request, f'Объект № {obj.number} скрыт.')
        return redirect('master_dashboard')
    else:
        messages.success(
            request, f'Объект № {obj.number} восстановлен из скрытых.')
        return redirect('hidden_objects')


@login_required
@user_passes_test(is_master, login_url='/')
@require_POST
def create_empty_object_view(request):
    """Создание пустого объекта"""
    number = request.POST.get('number', '').strip()
    title = request.POST.get('title', '').strip()

    if not number:
        messages.error(request, 'Номер объекта обязателен для заполнения')
        return redirect('master_dashboard')

    in_queue_status = ObjectStatus.objects.filter(
        title="В очереди").first()

    obj = Object.objects.create(
        number=number,
        title=title,
        status=in_queue_status
    )

    messages.success(request, f'Пустой объект № {obj.number} успешно создан!')
    return redirect('master_dashboard')


@login_required
@user_passes_test(is_master, login_url='/')
def object_detail_view(request, hashed_id):
    """Страница деталей объекта со списком изделий и их экземпляров"""
    object_id = decode_id(hashed_id)
    if object_id is None:
        raise Http404("Объект не найден")
    obj = get_object_or_404(Object, pk=object_id)
    referer_url = request.META.get('HTTP_REFERER')
    if not referer_url:
        referer_url = reverse('master_dashboard')
    if 'object_extra' in referer_url:
        if obj.is_hidden:
            referer_url = reverse('hidden_objects')
        else:
            referer_url = reverse('master_dashboard')
    else:
        if 'items-in-work' in referer_url:
            referer_url = reverse('items_in_work')
        elif 'hidden-objects' in referer_url:
            referer_url = reverse('hidden_objects')
        else:
            referer_url = reverse('master_dashboard')
    query = request.GET.get('q', '').strip()
    products = Product.objects.filter(object=obj).prefetch_related(
        'items__employee'
    )
    total_products_count = products.count()
    if query:
        products = products.annotate(full_name=Concat('title', Value(' '), 'part_name')).filter(
            Q(product_number__icontains=query) |
            Q(full_name__icontains=query)
        ).distinct()

    has_product_items = ProductItem.objects.filter(
        product__object=obj).exists()
    is_in_work = obj.status and obj.status.title == "В работе"

    employees = Employee.objects.all()

    context = {
        'object': obj,
        'products': products,
        'has_product_items': has_product_items,
        'is_in_work': is_in_work,
        'employees': employees,
        'back_url': referer_url,
        'search_query': query,
        'has_any_products': total_products_count > 0,
    }
    if request.headers.get('HX-Request'):
        return render(request, 'includes/object_detail_partial.html', context)
    return render(request, 'object_detail.html', context)


@login_required
@user_passes_test(is_master, login_url='/')
def object_extra_detail_view(request, hashed_id):
    """Страница с подробной информацией об объекте"""
    object_id = decode_id(hashed_id)
    if object_id is None:
        raise Http404("Объект не найден")

    obj = get_object_or_404(
        Object.objects.select_related('client').prefetch_related(
            'client__contacts', 'status'),
        pk=object_id
    )

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'edit_object':
            title = request.POST.get('title', '').strip()
            address = request.POST.get('address', '').strip()
            description = request.POST.get('description', '').strip()

            obj.title = title
            obj.address = address
            obj.description = description
            obj.save()

            messages.success(request, "Информация об объекте обновлена")

        elif action == 'select_client':
            client_id = request.POST.get('client_id')
            if client_id:
                client = get_object_or_404(Client, pk=client_id)
                obj.client = client
                messages.success(
                    request, f"Заказчик «{client.title}» успешно привязан")
            else:
                obj.client = None
                messages.info(request, "Заказчик отвязан от объекта")
            obj.save()

        elif action == 'create_client':
            title = request.POST.get('title', '').strip()
            description = request.POST.get('description', '').strip()

            if title:
                new_client = Client.objects.create(
                    title=title,
                    description=description
                )
                obj.client = new_client
                obj.save()
                messages.success(
                    request, f"Заказчик «{new_client.title}» создан и привязан к объекту")
            else:
                messages.error(
                    request, "Название заказчика не может быть пустым")

        elif action == 'add_contact':
            if obj.client:
                full_name = request.POST.get('full_name', '').strip()
                phone_numbers = request.POST.get('phone_numbers', '').strip()
                email = request.POST.get('email', '').strip()
                description = request.POST.get('description', '').strip()

                if full_name:
                    contact = ContactPerson.objects.create(
                        full_name=full_name,
                        phone_numbers=phone_numbers,
                        email=email,
                        description=description
                    )
                    obj.client.contacts.add(contact)
                    messages.success(
                        request, f"Контактное лицо «{full_name}» добавлено")
                else:
                    messages.error(
                        request, "ФИО контакта не может быть пустым")
            else:
                messages.error(
                    request, "Сначала необходимо привязать заказчика")

        elif action == 'edit_contact':
            contact_id = request.POST.get('contact_id')
            contact = get_object_or_404(ContactPerson, pk=contact_id)

            contact.full_name = request.POST.get('full_name', '').strip()
            contact.phone_numbers = request.POST.get(
                'phone_numbers', '').strip()
            contact.email = request.POST.get('email', '').strip()
            contact.description = request.POST.get('description', '').strip()
            contact.save()

            messages.success(
                request, f"Контакт «{contact.full_name}» обновлен")

        elif action == 'delete_contact':
            contact_id = request.POST.get('contact_id')
            contact = get_object_or_404(ContactPerson, pk=contact_id)
            full_name = contact.full_name
            contact.delete()
            messages.success(request, f"Контактное лицо «{full_name}» удалено")

        return redirect('object_extra_detail', hashed_id=hashed_id)

    all_clients = Client.objects.all().order_by('title')

    context = {
        'object': obj,
        'client': obj.client,
        'all_clients': all_clients,
        'contacts': obj.client.contacts.all() if obj.client else [],
    }
    return render(request, 'object_extra_detail.html', context)


@login_required
@user_passes_test(is_master, login_url='/')
def hidden_objects(request):
    """Страница скрытых объектов"""
    query = request.GET.get('q', '').strip()
    objects = Object.objects.filter(is_hidden=True).select_related('client')
    if query:
        objects = objects.filter(
            Q(number__icontains=query) |
            Q(client__title__icontains=query) |
            Q(title__icontains=query)
        ).distinct()
    context = {
        'objects': objects,
        'search_query': query,
    }
    if request.headers.get('HX-Request'):
        return render(request, 'includes/hidden_objects_table_partial.html', context)
    return render(request, 'hidden_objects.html', context)


MONTH_NAMES = {
    1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
    5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
    9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'
}


@login_required
@user_passes_test(is_master, login_url='/')
def workers_stats(request):
    """Статистика работников с переключением по месяцам"""
    now = timezone.now()

    try:
        selected_year = int(request.GET.get('year', now.year))
        selected_month = int(request.GET.get('month', now.month))
        if not (1 <= selected_month <= 12):
            raise ValueError
    except (ValueError, TypeError):
        selected_year = now.year
        selected_month = now.month

    if selected_month == 1:
        prev_year = selected_year - 1
        prev_month = 12
    else:
        prev_year = selected_year
        prev_month = selected_month - 1

    if selected_month == 12:
        next_year = selected_year + 1
        next_month = 1
    else:
        next_year = selected_year
        next_month = selected_month + 1

    monthly_stats_qs = ProductItem.objects.filter(
        status=ProductItem.StatusChoices.COMPLETED,
        end_time__year=selected_year,
        end_time__month=selected_month
    ).values(
        'employee__id',
        'employee__name',
    ).annotate(
        total_quantity=Sum('quantity'),
        total_earned=Sum(
            ExpressionWrapper(
                F('quantity') * F('product__payment'),
                output_field=FloatField()
            )
        )
    ).order_by('-total_earned')

    monthly_stats = list(monthly_stats_qs)
    for stat in monthly_stats:
        if stat['employee__id']:
            stat['employee__hashid'] = encode_id(stat['employee__id'])

    total_month_payout = sum(
        item['total_earned'] or 0 for item in monthly_stats)

    context = {
        'monthly_stats': monthly_stats,
        'total_month_payout': total_month_payout,
        'selected_year': selected_year,
        'selected_month': selected_month,
        'selected_month_name': MONTH_NAMES.get(selected_month, ''),
        'prev_year': prev_year,
        'prev_month': prev_month,
        'next_year': next_year,
        'next_month': next_month,
    }
    if request.headers.get('HX-Request'):
        return render(request, 'includes/workers_stats_partial.html', context)
    return render(request, 'workers_stats.html', context)


@login_required
@user_passes_test(is_master, login_url='/')
def all_workers_stats(request):
    """Страница со статистикой всех работников за всё время"""
    employees_stats = Employee.objects.annotate(
        total_quantity=Coalesce(
            Sum(
                'product_items__quantity',
                filter=Q(product_items__status=ProductItem.StatusChoices.COMPLETED)
            ),
            Value(Decimal('0.0'))
        ),
        total_earned=Coalesce(
            Sum(
                ExpressionWrapper(
                    F('product_items__quantity') *
                    F('product_items__product__payment'),
                    output_field=FloatField()
                ),
                filter=Q(product_items__status=ProductItem.StatusChoices.COMPLETED)
            ),
            Value(0.0)
        )
    ).select_related('user').order_by('-total_earned', 'name')

    context = {
        'employees_stats': employees_stats,
    }
    if request.headers.get('HX-Request'):
        return render(request, 'includes/all_workers_partial.html', context)
    return render(request, 'all_workers.html', context)


@login_required
@user_passes_test(is_master, login_url='/')
@require_POST
def create_worker(request):
    """Создание нового аккаунта работника"""
    name = request.POST.get('name', '').strip()
    password = request.POST.get('password', '').strip()

    if not name or not password:
        messages.error(request, 'Заполните все поля')
        return redirect('all_workers')

    user = User.objects.create_user(
        username=name,
        password=password,
    )

    worker_group, _ = Group.objects.get_or_create(name='worker')
    user.groups.add(worker_group)

    Employee.objects.create(
        user=user,
        name=name
    )

    messages.success(request, f'Работник "{name}" успешно создан!')
    return redirect('all_workers_stats')


@login_required
@user_passes_test(is_master, login_url='/')
@require_POST
def deactivate_worker(request, employee_id):
    """Деактивация пользователя Django, привязанного к работнику"""
    employee = get_object_or_404(Employee, pk=employee_id)

    if employee.user:
        employee.user.is_active = False
        employee.user.save()
        messages.success(
            request, f'Пользователь работника "{employee.name}" деактивирован.')
    else:
        messages.warning(
            request, f'У работника "{employee.name}" нет привязанного пользователя Django.')

    return redirect('all_workers_stats')


@login_required
@user_passes_test(is_master, login_url='/')
def worker_detail_view(request, hashed_id):
    """Страница деталей работника со списком изделий за выбранный месяц"""
    employee_id = decode_id(hashed_id)
    if employee_id is None:
        raise Http404("Работник не найден")

    employee = get_object_or_404(Employee, pk=employee_id)
    now = timezone.now()

    referer_url = request.META.get('HTTP_REFERER')
    current_path = reverse('worker_detail', kwargs={'hashed_id': hashed_id})
    if not referer_url or current_path in referer_url:
        back_url = reverse('all_workers_stats')
    else:
        back_url = referer_url

    try:
        selected_year = int(request.GET.get('year', now.year))
        selected_month = int(request.GET.get('month', now.month))
        if not (1 <= selected_month <= 12):
            raise ValueError
    except (ValueError, TypeError):
        selected_year = now.year
        selected_month = now.month

    if selected_month == 1:
        prev_year = selected_year - 1
        prev_month = 12
    else:
        prev_year = selected_year
        prev_month = selected_month - 1

    if selected_month == 12:
        next_year = selected_year + 1
        next_month = 1
    else:
        next_year = selected_year
        next_month = selected_month + 1

    queued_items = ProductItem.objects.filter(
        employee=employee,
        status=ProductItem.StatusChoices.QUEUED
    ).select_related('product', 'product__object').order_by('id')

    items = ProductItem.objects.filter(
        employee=employee,
        start_time__year=selected_year,
        start_time__month=selected_month
    ).select_related('product', 'product__object').order_by('-start_time')

    total_quantity = items.aggregate(total=Sum('quantity'))['total'] or 0
    total_earned = sum(item.total_payment for item in items)

    context = {
        'employee': employee,
        'items': items,
        'queued_items': queued_items,
        'total_quantity': total_quantity,
        'total_earned': total_earned,
        'selected_year': selected_year,
        'selected_month': selected_month,
        'selected_month_name': MONTH_NAMES.get(selected_month, ''),
        'prev_year': prev_year,
        'prev_month': prev_month,
        'next_year': next_year,
        'next_month': next_month,
        'back_url': back_url,
    }

    if request.headers.get('HX-Request'):
        return render(request, 'includes/worker_detail_partial.html', context)

    return render(request, 'worker_detail.html', context)


@login_required
def top_workers_view(request):
    """Страница топа сотрудников по сумме оплаты за месяц"""
    now = timezone.now()
    selected_year = int(now.year)
    selected_month = int(now.month)

    top_workers = ProductItem.objects.filter(
        status=ProductItem.StatusChoices.COMPLETED,
        end_time__year=selected_year,
        end_time__month=selected_month
    ).values(
        'employee__id',
        'employee__name',
    ).annotate(
        total_quantity=Sum('quantity'),
        total_earned=Coalesce(
            Sum(
                ExpressionWrapper(
                    F('quantity') * F('product__payment'),
                    output_field=FloatField()
                )
            ),
            Value(0.0)
        )
    ).order_by('-total_earned')

    context = {
        'top_workers': top_workers,
        'is_master': is_master(request.user),
        'is_worker': is_worker(request.user),
    }
    return render(request, 'top_workers.html', context)


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
    query = request.GET.get('q', '').strip()
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
        available_qty=ExpressionWrapper(
            F('quantity') - F('used_quantity'),
            output_field=DecimalField()
        )
    ).filter(
        available_qty__gt=0
    ).select_related('object').order_by('product_number')

    if query:
        available_products = available_products.annotate(
            full_name=Concat('title', Value(' '), 'part_name')
        ).filter(
            Q(product_number__icontains=query) |
            Q(full_name__icontains=query)
        ).distinct()

    queued_items = ProductItem.objects.filter(
        status=ProductItem.StatusChoices.QUEUED,
        employee__user=request.user
    ).select_related('product', 'product__object', 'employee')

    context = {
        'available_products': available_products,
        'queued_items': queued_items,
        'search_query': query,
    }
    if request.headers.get('HX-Request'):
        return render(request, 'includes/employee_dashboard_partial.html', context)
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
