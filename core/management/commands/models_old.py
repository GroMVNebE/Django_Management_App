from django.db import models
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.db.models import UniqueConstraint, Prefetch
from django.db.models.functions import Lower
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import datetime, timedelta
from decimal import Decimal
from django.contrib.auth.models import User, Group

# OLD


def validate_deadline(deadline: datetime.date):
    if deadline < timezone.now().date():
        raise ValidationError(
            f"Дата сдачи объекта ({deadline}) не может быть ранее текущего дня ({timezone.now().date()})")


def validate_worker(worker):
    if not worker.groups.filter(name='workers').exists():
        raise ValidationError(f"{worker.username} не является рабочим")


class Product(models.Model):
    """Модель, описывающая изделие"""
    prod_number = models.CharField(
        verbose_name="Номер изделия",
        max_length=255,
        help_text="Введите код (номер) изделия (пр. 01). Полный номер вводить не нужно",
    )

    object = models.ForeignKey('Object', on_delete=models.CASCADE,
                               verbose_name="Объект, которому принадлежит изделие")

    name = models.CharField(
        verbose_name="Название изделия",
        max_length=1024,
        help_text="Введите название изделия"
    )

    amount = models.IntegerField(
        verbose_name="Кол-во",
        validators=[MinValueValidator(
            limit_value=1, message="Значение должно быть не меньше 1")],
        help_text="Введите количество изготавливаемых изделий (не меньше 1)",
        default=1,
        blank=True
    )

    description = models.TextField(
        verbose_name="Примечание",
        max_length=8096,
        help_text="Введите примечание к изделию",
        blank=True,
        null=True
    )

    price = models.BigIntegerField(verbose_name="Стоимость", validators=[MinValueValidator(
        limit_value=1, message="Значение должно быть не меньше 1")], help_text="Введите цену изделия (не меньше 1)",
        default=1)

    ava_amount = models.IntegerField(
        verbose_name="Доступное кол-во", validators=[MinValueValidator(0)], null=True, blank=True)

    completed_amount = models.DecimalField(verbose_name="Произведённое кол-во", validators=[
                                           MinValueValidator(0)], null=True, blank=True, max_digits=10, decimal_places=1)

    def __str__(self):
        return f"{self.object}-{self.prod_number} {self.name}"

    def get_absolute_url(self):
        return reverse("product-detail", args=[str(self.id)])

    def get_master_url(self):
        return reverse("product-in-work", args=[str(self.id)])

    def get_id(self):
        return f"{self.object}-{self.prod_number}"

    def get_deadline_days(self):
        return self.object.get_deadline_days()

    def get_ava_amount(self, preloaded_instances=None, preloaded_parts=None):
        if self.ava_amount is not None:
            return self.ava_amount

        amount = 0
        if preloaded_instances is not None:
            # Фильтруем в памяти Python
            instances = [
                inst for inst in preloaded_instances if inst.product_id == self.id]
        else:
            instances = CreationInstance.objects.filter(product=self)

        for instance in instances:
            if instance.product_id:
                amount += instance.amount

        max_amount = 0
        if preloaded_parts is not None:
            parts = [part for part in preloaded_parts if part.product_id == self.id]
        else:
            parts = Part.objects.filter(product=self)

        for part in parts:
            busy = part.get_all_amount(
                preloaded_instances=preloaded_instances) / part.amount
            busy = int(busy) + 1 if int(busy) != busy else int(busy)
            max_amount = max(max_amount, busy)

        amount += max_amount
        calculated_ava = self.amount - amount

        if preloaded_instances is None:
            self.ava_amount = calculated_ava
            self.save(update_fields=['ava_amount'])

        return calculated_ava

    def ava_float(self):
        amount = 0
        instances = CreationInstance.objects.filter(product=self)
        for instance in instances:
            if instance.product:
                amount += instance.amount
        max_amount = 0
        parts = Part.objects.filter(product=self)
        for part in parts:
            busy = part.get_all_amount() / part.amount
            max_amount = max(max_amount, busy)
        amount += max_amount
        return self.amount - amount

    def get_in_work_amount(self):
        amount = 0
        instances = CreationInstance.objects.filter(
            product=self, status='IN_WORK')
        for instance in instances:
            amount += instance.amount
        return amount

    def get_in_work_by_parts_amount(self):
        min_amount = -1
        parts = Part.objects.filter(product=self)
        for part in parts:
            in_work = part.get_in_work_amount() / part.amount
            in_work = int(in_work)
            if min_amount == -1:
                min_amount = in_work
            else:
                min_amount = min(min_amount, in_work)
        min_amount = max(min_amount, 0)
        return min_amount

    def get_in_work_all_amount(self):
        return self.get_in_work_amount() + self.get_in_work_by_parts_amount()

    def get_parts_in_work_amount(self):
        parts = Part.objects.filter(product=self)
        amount = 0
        for part in parts:
            amount += part.get_in_work_amount()
        return amount

    def get_ava_parts_amount(self):
        parts = Part.objects.filter(product=self)
        amount = 0
        for part in parts:
            amount += part.get_ava_amount()
        return amount

    def get_completed_amount(self):
        if self.completed_amount != None:
            return int(self.completed_amount)
        amount = 0
        products = CreationInstance.objects.filter(
            product=self, status='COMPLETED')
        for product in products:
            amount += product.amount
        parts = Part.objects.filter(product=self)
        min_amount = -1
        for part in parts:
            if min_amount == -1:
                min_amount = Decimal(part.get_completed_amount()) / part.amount
            else:
                min_amount = min(
                    min_amount, Decimal(part.get_completed_amount()) / part.amount)
        min_amount = max(min_amount, 0)
        amount = Decimal(amount) + min_amount
        self.completed_amount = amount
        self.save()
        return int(self.completed_amount)

    def completed_float(self):
        amount = Decimal(0)
        products = CreationInstance.objects.filter(
            product=self, status='COMPLETED')
        for product in products:
            amount += product.amount
        parts = Part.objects.filter(product=self)
        min_amount = Decimal(-1)
        for part in parts:
            if min_amount == -1:
                min_amount = Decimal(part.get_completed_amount()) / part.amount
            else:
                min_amount = min(
                    min_amount, Decimal(part.get_completed_amount()) / part.amount)
        min_amount = max(min_amount, 0)
        amount = amount + min_amount
        return amount

    def get_full_completed(self):
        amount = 0
        products = CreationInstance.objects.filter(
            product=self, status='COMPLETED')
        for product in products:
            amount += product.amount
        return amount

    def get_completed_parts_amount(self):
        amount = 0
        parts = Part.objects.filter(product=self)
        for part in parts:
            amount += part.get_completed_amount()
        return amount

    class Meta:
        app_label = 'workspace'
        ordering = ['object', 'prod_number']


class Part(models.Model):
    """Модель, описывающая часть изделия"""
    name = models.CharField(
        verbose_name="Название части",
        max_length=1024,
        help_text="Введите название части"
    )

    product = models.ForeignKey(Product, on_delete=models.CASCADE)

    amount = models.IntegerField(
        verbose_name="Кол-во",
        validators=[MinValueValidator(
            limit_value=1, message="Значение должно быть не меньше 1")],
        help_text="Введите количество частей, используемых в изделии",
        default=1,
        blank=True
    )

    price = models.DecimalField(
        verbose_name="Стоимость",
        validators=[MinValueValidator(
            limit_value=1, message="Значение должно быть не меньше 1")],
        help_text="Введите сумму, которая будет выплачена работнику за изготовление части",
        max_digits=12, decimal_places=2
    )

    ava_amount = models.DecimalField(verbose_name="Доступное кол-во", validators=[
                                     MinValueValidator(0)], max_digits=10, decimal_places=1, null=True, blank=True)

    def get_in_work_amount(self):
        amount = 0
        parts = CreationInstance.objects.filter(part=self, status='IN_WORK')
        for part in parts:
            amount += part.amount
        return amount

    def get_all_amount(self, preloaded_instances=None):
        if preloaded_instances is not None:
            instances = [
                inst for inst in preloaded_instances if inst.part_id == self.id]
        else:
            instances = CreationInstance.objects.filter(part=self)

        return sum(inst.amount for inst in instances)

    def get_ava_amount(self, preloaded_instances=None):
        if self.ava_amount is not None:
            return self.ava_amount

        ava_amount = self.product.amount

        if preloaded_instances is not None:
            prod_instances = [
                inst for inst in preloaded_instances if inst.product_id == self.product_id]
            part_instances = [
                inst for inst in preloaded_instances if inst.part_id == self.id]
        else:
            prod_instances = CreationInstance.objects.filter(
                product=self.product)
            part_instances = CreationInstance.objects.filter(part=self)

        for instance in prod_instances:
            ava_amount -= instance.amount

        ava_amount *= self.amount

        for part_inst in part_instances:
            ava_amount -= part_inst.amount

        if preloaded_instances is None:
            self.ava_amount = ava_amount
            self.save(update_fields=['ava_amount'])

        return ava_amount

    def get_completed_amount(self):
        amount = 0
        parts = CreationInstance.objects.filter(part=self, status='COMPLETED')
        for part in parts:
            amount += part.amount
        return amount

    def get_id(self):
        return self.product.get_id()

    def get_deadline_days(self):
        return self.product.get_deadline_days()

    def __str__(self):
        return f'{self.product.get_id()} {self.name}'

    class Meta:
        app_label = 'workspace'


class ObjectState(models.Model):
    """Модель, описывающая состояние объекта"""
    name = models.CharField(
        verbose_name="Состояние",
        max_length=1024,
        unique=True,
        help_text="Введите состояние объекта"
    )

    priority = models.PositiveSmallIntegerField(
        verbose_name="Приоритет",
        unique=True,
        help_text="Введите приоритет состояния. У объектов будет отображаться состояние с наибольшим приоритетом"
    )

    group = models.CharField(
        verbose_name="Категория",
        max_length=255,
        choices=[('Payment', 'Оплата'), ('Purchasing',
                                         'Закупка'), ('Processing', 'Работа')],
        help_text="Выберите категорию, к которой относится состояние"
    )

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("state-detail", args=[str(self.id)])

    class Meta:
        app_label = 'workspace'
        constraints = [
            UniqueConstraint(
                Lower('name'),
                name='state_name_case_insensitive_unique',
                violation_error_message="Такое состояние уже существует"
            ),
            UniqueConstraint(
                'priority',
                name="state_priority_unique",
                violation_error_message="Состояние с таким приоритетом уже существует"
            ),
        ]
        ordering = ['priority']


class Object(models.Model):
    """Модель, описывающая объект"""
    obj_number = models.CharField(
        verbose_name="Номер объекта",
        max_length=255,
        help_text="Введите код (номер) объекта (пр. 1234-56)",
    )

    created_at = models.DateField(
        verbose_name="Дата создания объекта",
    )

    deadline = models.DateField(
        verbose_name="Дата сдачи объекта",
        help_text="Выберите дату сдачи объекта",
        validators=[validate_deadline],
        null=True,
        blank=True,
    )

    hidden = models.BooleanField(verbose_name="Скрытие объекта", default=False)

    ready_percentage = models.DecimalField(verbose_name="Процент готовности", null=True, blank=True,
                                           validators=[MinValueValidator(
                                               0), MaxValueValidator(100)],
                                           max_digits=5, decimal_places=2)

    def __str__(self):
        return self.obj_number

    def get_absolute_url(self):
        return reverse("object-detail", args=[str(self.id)])

    def get_deadline_date(self):
        return f'{self.deadline.day:02}.{self.deadline.month:02}.{self.deadline.year}'

    def get_products_amount(self):
        data = Product.objects.filter(object=self).all()
        amount = 0
        for product in data:
            amount += product.amount
        return amount

    def get_deadline_days(self):
        if self.deadline <= timezone.now().date():
            return 'Время выполнения истекло.'
        days_till_deadline = (self.deadline -
                              timezone.now().date()).days
        ret_str = str(days_till_deadline)
        if days_till_deadline >= 10 and days_till_deadline < 20:
            ret_str += ' дней'
        else:
            if days_till_deadline % 10 == 0 or days_till_deadline % 10 >= 5:
                ret_str += ' дней'
            elif days_till_deadline % 10 > 1:
                ret_str += ' дня'
            else:
                ret_str += ' день'

        st = timezone.now().date()
        weekends = 0
        while st < self.deadline:
            if st.weekday() in [5, 6]:
                weekends += 1
            st += timedelta(days=1)
        days_till_deadline -= weekends
        ret_str += f' ({days_till_deadline} раб.'
        if days_till_deadline >= 10 and days_till_deadline < 20:
            ret_str += ' дней'
        else:
            if days_till_deadline % 10 == 0 or days_till_deadline % 10 >= 5:
                ret_str += ' дней'
            elif days_till_deadline % 10 > 1:
                ret_str += ' дня'
            else:
                ret_str += ' день'
        ret_str += ')'
        return ret_str

    def get_ready_percentage(self):
        if self.ready_percentage != None:
            return int(self.ready_percentage)
        full_price = Decimal(0)
        ready_price = Decimal(0)
        products = Product.objects.filter(
            object=self).prefetch_related(Prefetch('part_set', queryset=Part.objects.all()))
        all_amount = 0
        compl_amount = 0
        for product in products:
            full_price += product.price * product.amount
            ready_price += product.price * product.get_full_completed()
            all_amount += product.amount
            compl_amount += product.get_full_completed()
            for part in product.part_set.all():
                ready_price += part.price * part.get_completed_amount()
                all_amount += part.amount * product.amount
                compl_amount += part.get_completed_amount()

        if all_amount == compl_amount:
            self.ready_percentage = 100
            self.save()
            return int(self.ready_percentage)
        if compl_amount == 0:
            self.ready_percentage = 0
            self.save()
            return int(self.ready_percentage)

        if full_price != 0:
            res = (ready_price / full_price) * 100
            res = max(0, res)
            res = min(res, 100)
            self.ready_percentage = res
            self.save()
        else:
            self.ready_percentage = 0
            self.save()
            res = 0
        return int(self.ready_percentage)

    def get_state_color(self):
        states = ObjectStateInstance.objects.filter(object_id=self.id)
        color = 'none'
        for state in states:
            if state.state.name == "Приостановлен":
                color = 'chocolate'
            elif state.state.name == "В сборке":
                color = 'chartreuse'
                break
        return color

    class Meta:
        app_label = 'workspace'
        ordering = ['obj_number']


class ObjectStateInstance(models.Model):
    """Модель, описывающая экземпляр состояния объекта (для отслеживания изменений)"""

    object = models.ForeignKey(
        Object, on_delete=models.CASCADE, verbose_name="Объект", help_text="Выберите объект, которому принадлежит состояние")

    state = models.ForeignKey(
        ObjectState, help_text="Выберите состояни(е/я) объекта", verbose_name="Состояние", on_delete=models.CASCADE)

    created_at = models.DateField(
        verbose_name="Дата создания", help_text="Выберите дату добавления состояния")

    class Meta:
        app_label = 'workspace'
        ordering = ['object', 'state']


class WorkerData(models.Model):
    """Модель, описывающая данные работника"""
    worker = models.ForeignKey(get_user_model(
    ), on_delete=models.DO_NOTHING, null=True, blank=True, verbose_name="Работник", validators=[validate_worker])

    display_name = models.CharField(
        max_length=256, verbose_name="Отображаемое имя")

    def get_payment(self):
        creations = CreationInstance.objects.filter(
            worker=self, completed__gte=timezone.now().date().replace(day=1), status='COMPLETED')
        pay = 0
        for creation in creations:
            pay += creation.get_price()
        return pay

    def get_completed_amount(self):
        creations = CreationInstance.objects.filter(
            worker=self, completed__gte=timezone.now().date().replace(day=1), status='COMPLETED')
        amount = 0
        for creation in creations:
            amount += creation.amount
        return amount

    def get_all_payment(self):
        creations = CreationInstance.objects.filter(
            worker=self, status='COMPLETED').all()
        pay = 0
        for creation in creations:
            pay += creation.get_price()
        return pay

    def get_all_completed_amount(self):
        creations = CreationInstance.objects.filter(
            worker=self, status='COMPLETED').all()
        amount = 0
        for creation in creations:
            amount += creation.amount
        return amount

    def get_completed(self, start, end):
        creations = CreationInstance.objects.filter(
            worker=self, completed__gte=start, completed__lte=end, status='COMPLETED')
        amount = 0
        for creation in creations:
            amount += creation.amount
        return amount

    def get_payment(self, start, end):
        creations = CreationInstance.objects.filter(
            worker=self, completed__gte=start, completed__lte=end, status='COMPLETED')
        pay = 0
        for creation in creations:
            pay += creation.get_price()
        return pay

    def get_absolute_url(self):
        return reverse("worker", args=[str(self.id)])

    def __str__(self):
        return self.display_name

    class Meta:
        app_label = 'workspace'


class CreationInstance(models.Model):
    """Модель, описывающая экземпляр изделия/детали"""
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, null=True, blank=True)
    part = models.ForeignKey(
        Part, on_delete=models.CASCADE, null=True, blank=True)
    worker = models.ForeignKey(
        WorkerData, on_delete=models.DO_NOTHING, null=False, verbose_name="Работник")
    amount = models.DecimalField(
        verbose_name="Кол-во",
        validators=[MinValueValidator(
            limit_value=0.1, message="Значение должно быть не меньше 0.1")],
        help_text="Введите количество изготавливаемых изделий (не меньше 0.1)",
        default=1,
        blank=True,
        decimal_places=1,
        max_digits=10
    )
    status = models.CharField(
        choices=[('QUEUED', 'QUEUED'), ('IN_WORK', 'IN_WORK'), ('COMPLETED', 'COMPLETED')], max_length=255)
    queued = models.DateTimeField(
        verbose_name="Дата добавления в очередь", null=True, blank=True)
    started = models.DateTimeField(
        verbose_name="Дата начала изготовления", null=True, blank=True)
    completed = models.DateTimeField(
        verbose_name="Дата окончания изготовления", null=True, blank=True)

    def get_absolute_url(self):
        return reverse("my-product", args=[str(self.id)])

    def get_master_url(self):
        return reverse("instance-details", args=[str(self.id)])

    def get_queue_url(self):
        return reverse("queued-details", args=[str(self.id)])

    def get_price(self):
        if self.product:
            return self.product.price * self.amount
        elif self.part:
            return self.part.price * self.amount

    def __str__(self):
        if self.product:
            return self.product.__str__()
        else:
            return self.part.__str__()

    class Meta:
        app_label = 'workspace'
        ordering = ['queued', 'product', 'part']


class Question(models.Model):
    """Модель, описывающая вопрос по изделию"""
    instance = models.ForeignKey(
        CreationInstance, on_delete=models.CASCADE, null=False, verbose_name="Изделие в работе")

    quest = models.TextField(
        verbose_name="Вопрос",
        max_length=2048,
        help_text="Введите вопрос по поводу изготовления изделия",
    )

    answer = models.TextField(
        verbose_name="Ответ",
        max_length=2048,
        help_text="Введите ответ на вопрос",
    )

    class Meta:
        app_label = 'workspace'
        ordering = ['instance']


class ParseBlacklistValue(models.Model):
    """Модель, описывающая значение в чёрном списке парсинга"""
    value = models.CharField(
        max_length=256, verbose_name="Игнорируемое значение")

    def __str__(self):
        return self.value

    class Meta:
        app_label = 'workspace'


class Notification(models.Model):
    recipient_group = models.ForeignKey(Group, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    read_by = models.ManyToManyField(User, blank=True)

    class Meta:
        app_label = 'workspace'
        ordering = ['-created_at']

    def __str__(self):
        return f"Уведомление для {self.recipient_group.name}: {self.title} {self.message}"
