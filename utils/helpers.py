from rest_framework.exceptions import ValidationError


def validate_required_fields(data: dict, required_fields: dict):

    errors = {}

    for field, label in required_fields.items():
        if not data.get(field):
            errors[field] = f"{label} is required and cannot be empty."

    if errors:
        raise ValidationError(errors)

def get_base_url(scope):
    scheme = scope.get("scheme", "http")
    headers = dict(scope.get("headers", []))
    host = headers.get(b"host", b"").decode()
    return f"{scheme}://{host}"