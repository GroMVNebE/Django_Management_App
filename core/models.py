from django.db import models
from django.db.models import Sum
from django.conf import settings
from django.core.validators import MinValueValidator
from django.urls import reverse
from .utils import encode_id
from decimal import Decimal


class Client(models.Model):
    """Модель заказчика"""
    title = models.CharField(max_length=255, verbose_name="Название")
    description = models.TextField(blank=True, verbose_name="Описание")
    contacts = models.ManyToManyField(
        'ContactPerson',
        related_name='clients',
        blank=True,
        verbose_name="Контактные лица"
    )

    def __str__(self):
        return self.title


class ContactPerson(models.Model):
    """Модель контактного лица"""
    full_name = models.CharField(max_length=512, verbose_name="ФИО")
    phone_numbers = models.TextField(verbose_name="Номера телефона")
    email = models.EmailField(blank=True, verbose_name="Email")
    description = models.TextField(blank=True, verbose_name="Описание")

    def __str__(self):
        return self.full_name


class ObjectStatus(models.Model):
    """Состояние объекта"""
    title = models.CharField(max_length=255, verbose_name="Название")
    priority = models.IntegerField(default=0, verbose_name="Приоритет")

    class Meta:
        ordering = ['priority']

    def __str__(self):
        return self.title


class Object(models.Model):
    """Модель объекта"""
    title = models.CharField(
        max_length=255, verbose_name="Название", blank=True)
    address = models.TextField(verbose_name="Адрес", blank=True)
    number = models.CharField(
        max_length=16,
        verbose_name="Номер (пр. 1234-56)"
    )
    status = models.ManyToManyField(
        ObjectStatus,
        related_name='linked_objects',
        blank=True,
        verbose_name="Состояние объекта"
    )
    is_hidden = models.BooleanField(default=False, verbose_name="Скрыт")
    client = models.ForeignKey(
        Client,
        on_delete=models.SET_NULL,
        null=True,
        related_name='linked_objects',
        verbose_name="Заказчик"
    )
    description = models.TextField(blank=True, verbose_name="Описание")

    @property
    def hashid(self):
        return encode_id(self.pk)

    def get_absolute_url(self):
        return reverse('object_detail', kwargs={'hashed_id': self.hashid})

    def __str__(self):
        return f"{self.number} {self.title}"


class Product(models.Model):
    """Модель изделия"""
    object = models.ForeignKey(
        Object,
        on_delete=models.CASCADE,
        related_name='products',
        verbose_name="Объект"
    )
    product_number = models.CharField(
        max_length=32, verbose_name="Номер изделия")
    title = models.CharField(max_length=255, verbose_name="Название изделия")
    part_name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Название части"
    )
    quantity = models.PositiveIntegerField(
        default=1, verbose_name="Количество")
    payment = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal(0.00),
        verbose_name="Оплата"
    )

    @property
    def available_quantity(self):
        """Оставшееся доступное количество изделия для изготовления"""
        used_qty = self.items.aggregate(
            total=Sum('quantity')
        )['total'] or Decimal('0.0')

        remaining = Decimal(self.quantity) - used_qty
        return max(remaining, Decimal('0.0'))

    def __str__(self):
        if self.part_name:
            return f"{self.product_number} — {self.title} ({self.part_name})"
        return f"{self.product_number} — {self.title}"

    class Meta:
        ordering = ['product_number']


class Employee(models.Model):
    """Модель работника"""
    name = models.CharField(max_length=255, verbose_name="Имя")
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='employee_profile',
        verbose_name="Пользователь Django"
    )

    def __str__(self):
        return self.name


class ProductItem(models.Model):
    """Экземпляр изделия"""
    class StatusChoices(models.TextChoices):
        QUEUED = 'queued', 'В очереди'
        IN_PROGRESS = 'in_progress', 'В процессе изготовления'
        COMPLETED = 'completed', 'Готов'

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name='items',
        verbose_name="Изделие"
    )
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=1,
        default=Decimal('1.0'),
        validators=[MinValueValidator(Decimal('0.1'))],
        verbose_name="Количество"
    )
    status = models.CharField(
        max_length=20,
        choices=StatusChoices.choices,
        default=StatusChoices.IN_PROGRESS,
        verbose_name="Статус"
    )
    start_time = models.DateTimeField(
        null=True, blank=True, verbose_name="Дата и время начала")
    end_time = models.DateTimeField(
        null=True, blank=True, verbose_name="Дата и время окончания")
    employee = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='product_items',
        verbose_name="Работник"
    )

    def __str__(self):
        return f"{self.product} {self.employee}"


class ParsingBlacklist(models.Model):
    """Запись в чёрном списке для парсинга"""
    value = models.CharField(
        max_length=255, verbose_name="Значение в чёрном списке")

    def __str__(self):
        return self.value
