from django.contrib import admin
from social.models import *

# Register your models here.
admin.site.register(CoupleConnectionModel)
admin.site.register(CoupleModel)
admin.site.register(CoupleMembershipModel)
admin.site.register(PhotoAlbumModel)
admin.site.register(SingleConnectionModel)
admin.site.register(UserPreferenceModel)
admin.site.register(CoupleMomentModel)
admin.site.register(CoupleMomentPhotoModel)

