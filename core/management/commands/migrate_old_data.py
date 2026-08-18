from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import (
    Client,
    ContactPerson,
    Employee,
    Object as NewObject,
    ObjectStatus,
    ParsingBlacklist,
    Product as NewProduct,
    ProductItem,
)

from . import models_old as old_models


class Command(BaseCommand):
    help = "Миграция данных из старой структуры БД в новую"

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("Начало переносa данных..."))

        self.stdout.write("Перенос чёрного списка парсинга...")
        old_blacklist = old_models.ParseBlacklistValue.objects.using(
            'old_db').all()
        for item in old_blacklist:
            ParsingBlacklist.objects.get_or_create(value=item.value)

        self.stdout.write("Перенос состояний объектов...")
        status_map = {}  # {old_state_id: new_status_instance}
        old_states = old_models.ObjectState.objects.using('old_db').all()
        for state in old_states:
            new_status, _ = ObjectStatus.objects.get_or_create(
                priority=state.priority,
                defaults={'title': state.name}
            )
            status_map[state.id] = new_status

        self.stdout.write("Перенос работников...")
        employee_map = {}
        old_workers = old_models.WorkerData.objects.using('old_db').all()
        for worker in old_workers:
            employee, _ = Employee.objects.get_or_create(
                user=worker.worker,
                defaults={'name': worker.display_name}
            )
            employee_map[worker.id] = employee

        self.stdout.write("Перенос объектов...")
        object_map = {}
        old_objects = old_models.Object.objects.using('old_db').all()
        in_work_st = ObjectStatus.objects.get(title='В работе')
        in_queue_st = ObjectStatus.objects.get(title='В очереди')
        for obj in old_objects:
            state_instances = old_models.ObjectStateInstance.objects.using(
                'old_db').filter(object=obj)
            new_obj = NewObject.objects.create(
                number=obj.obj_number,
                title="",
                address="",
                is_hidden=obj.hidden,
                description="",
                status=in_work_st if str(
                    state_instances[0].state) == 'В сборке' else in_queue_st
            )
            object_map[obj.id] = new_obj

        self.stdout.write("Перенос изделий и деталей...")
        product_map = {}
        part_map = {}

        old_products = old_models.Product.objects.using(
            'old_db').select_related('object').all()
        for old_prod in old_products:
            new_obj = object_map.get(old_prod.object_id)
            if not new_obj:
                continue

            old_parts = old_models.Part.objects.using(
                'old_db').filter(product=old_prod)
            if old_parts:
                for old_part in old_parts:
                    new_part_prod = NewProduct.objects.create(
                        object=new_obj,
                        product_number=new_obj.number + '-' + old_prod.prod_number,
                        title=old_prod.name,
                        part_name=old_part.name,
                        quantity=old_part.amount * old_prod.amount,
                        payment=old_part.price
                    )
                    part_map[old_part.id] = new_part_prod
            else:
                new_prod = NewProduct.objects.create(
                    object=new_obj,
                    product_number=new_obj.number + '-' + old_prod.prod_number,
                    title=old_prod.name,
                    part_name="",
                    quantity=old_prod.amount,
                    payment=Decimal(old_prod.price)
                )
                product_map[old_prod.id] = new_prod

        self.stdout.write("Перенос экземпляров в работе (CreationInstance)...")
        status_conversion = {
            'QUEUED': ProductItem.StatusChoices.QUEUED,
            'IN_WORK': ProductItem.StatusChoices.IN_PROGRESS,
            'COMPLETED': ProductItem.StatusChoices.COMPLETED,
        }

        old_creations = old_models.CreationInstance.objects.using(
            'old_db').all()
        items_to_create = []

        for creation in old_creations:
            target_product = None
            if creation.product_id and creation.product_id in product_map:
                target_product = product_map[creation.product_id]
            elif creation.part_id and creation.part_id in part_map:
                target_product = part_map[creation.part_id]

            if not target_product:
                continue

            employee = employee_map.get(creation.worker_id)
            new_status = status_conversion.get(
                creation.status, ProductItem.StatusChoices.IN_PROGRESS)

            start_time = creation.started or creation.queued

            items_to_create.append(
                ProductItem(
                    product=target_product,
                    quantity=creation.amount,
                    status=new_status,
                    start_time=start_time,
                    end_time=creation.completed,
                    employee=employee
                )
            )

        ProductItem.objects.bulk_create(items_to_create, batch_size=500)

        self.stdout.write(self.style.SUCCESS(
            "Миграция данных успешно завершена!"))
