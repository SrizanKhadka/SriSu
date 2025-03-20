from rest_framework import permissions
from chat.models import CoupleConnectionModel

class OwnerPermission(permissions.BasePermission):
    
    def has_object_permission(self, request, view, obj):
        user_phone_number = request.user.phone_number  # Assuming user has a phone_number field
        
        if isinstance(obj, CoupleConnectionModel):
            return user_phone_number in [obj.sender_number, obj.receiver_number]

        return False