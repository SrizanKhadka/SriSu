from django.contrib import admin

from chat.models import ChatRoom, MatrixRoomMapping, MatrixUserMapping

admin.site.register(ChatRoom)
admin.site.register(MatrixUserMapping)


@admin.register(MatrixRoomMapping)
class MatrixRoomMappingAdmin(admin.ModelAdmin):
    """Read-only durable revocation rows.

    A mapping may outlive its Django room specifically so the retry worker can
    remove both members from a remotely created Matrix room. The supported
    admin path must not erase or manually short-circuit that tombstone.
    """

    list_display = (
        "id",
        "chat_room",
        "state",
        "matrix_room_id",
        "attempts",
        "available_at",
        "updated_at",
    )
    readonly_fields = tuple(field.name for field in MatrixRoomMapping._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return True

    def has_delete_permission(self, request, obj=None):
        return False
