from django.contrib import admin
from social.models import *

# Register your models here.
admin.site.register(CoupleConnectionModel)
admin.site.register(CoupleModel)
@admin.register(CoupleMembershipModel)
class CoupleMembershipAdmin(admin.ModelAdmin):
    # Personal labels are edited only through the authenticated, revision-checked
    # setter API. Do not surface them in generic staff membership forms.
    exclude = ("partner_nickname", "partner_nickname_revision")

admin.site.register(PhotoAlbumModel)
admin.site.register(SingleConnectionModel)
admin.site.register(UserPreferenceModel)
admin.site.register(CoupleMomentModel)
admin.site.register(CoupleMomentPhotoModel)

