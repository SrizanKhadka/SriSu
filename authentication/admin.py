from django.contrib import admin
from authentication.models import *

# Register your models here.

admin.site.register(OtpModel)
admin.site.register(UserModel)
admin.site.register(UserPhotoAlbumModel)
admin.site.register(UserInterestModel)
admin.site.register(InterestCategory)
admin.site.register(InterestModel)