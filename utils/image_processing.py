from PIL import Image
from io import BytesIO
import pillow_heif
from django.core.files.base import ContentFile
import os

pillow_heif.register_heif_opener()

MAX_SIZE_MB = 5
MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024

def open_image(file):
    try:
        image = Image.open(file)
        return image
    except Exception as e:
        print(f"Error opening image: {e}")
        raise ValueError("Invalid image file") from e
    
def convert_to_jpeg(image,original_name):
    if image.mode in ("RGBA", "P"):
        image = image.convert("RGB")
        
    base_name = os.path.splitext(original_name)[0]  
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90,optimize=True)
    
    return ContentFile(buffer.getvalue(), name=f"{base_name}.jpg")

def resize_image(uploaded_image):
    if uploaded_image.size <= MAX_SIZE_BYTES:
        return uploaded_image # No resizing needed
    
    image = open_image(uploaded_image)
    
    if image and image.mode in ("RGBA", "P"):
        image = image.convert("RGB")
        
    max_width = 2000
    if image.width > max_width:
        ratio = max_width / image.width
        image = image.resize((max_width, int(image.height * ratio)), Image.LANCZOS)
    
    buffer = BytesIO()
    quality = 85
    
    while True:
        buffer.seek(0)
        buffer.truncate()
        image.save(buffer, format="JPEG", quality=quality,optimize=True)
        
        if buffer.tell() <= MAX_SIZE_BYTES or quality <= 35:
            break
        
    
    return ContentFile(buffer.getvalue(), name=uploaded_image.name)

def process_image(uploaded_image):
    extensino = os.path.splitext(uploaded_image.name)[1].lower()
    
    image = open_image(uploaded_image)
    if extensino not in [".png", ".jpg", ".jpeg"]:
        return convert_to_jpeg(image,uploaded_image.name)
    else:
        return resize_image(uploaded_image)