from rest_framework.exceptions import ValidationError
from social.models import CouplePlanModel
from .models import PlanCard
from .services import emit


def message_accepted(operation, plan_id):
    correlated = False
    if plan_id is not None:
        plan = CouplePlanModel.objects.filter(pk=plan_id, couple_id=operation.room.couple_id,
            audience_membership_ids=sorted([operation.room.first_membership_id, operation.room.second_membership_id])).first()
        if plan is None:
            raise ValidationError("Plan correlation is unavailable.")
        if plan.created_by_id == operation.actor_id:
            _, correlated = PlanCard.objects.get_or_create(plan=plan, defaults={"message_id": operation.target_id})
    if not correlated:
        emit("chat.message", operation.recipient_device.user, operation.pk, operation.target_id,
            room=operation.room, device=operation.recipient_device, change=operation.change)
