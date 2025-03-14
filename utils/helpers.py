from rest_framework.exceptions import ValidationError


def validate_required_fields(data: dict, required_fields: dict):

    errors = {}

    for field, label in required_fields.items():
        if not data.get(field):
            errors[field] = f"{label} is required and cannot be empty."

    if errors:
        raise ValidationError(errors)
