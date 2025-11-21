import os
from PIL import Image, ImageFilter, ImageOps

def create_folder_thumb(folder_path, output_path="folder.png"):
    # Configuration
    CANVAS_SIZE = (200, 300)
    THUMB_SIZE = (160, 245) # Slightly smaller than canvas to allow for rotation/padding
    MAX_COVERS = 4 # More than 4 looks messy in a stack
    
    # 1. Find Images
    valid_extensions = ('.jpg', '.jpeg', '.png', '.webp')
    images = [
        os.path.join(folder_path, f) 
        for f in os.listdir(folder_path) 
        if f.lower().endswith(valid_extensions)
    ]
    
    # Sort to ensure we get the first/last issues (depending on your naming convention)
    images.sort()
    
    # We only need the first few images for the stack effect
    # If you want the "Newest" on top, reverse this list or sort accordingly
    selected_images = images[:MAX_COVERS]
    
    if not selected_images:
        print("No images found.")
        return

    # 2. Create Canvas (Transparent Background)
    final_canvas = Image.new('RGBA', CANVAS_SIZE, (0, 0, 0, 0))

    # 3. Define Rotation Angles (Back to Front)
    # The last item in this list is the Front Cover (0 degrees)
    angles = [12, -8, 5, 0] 
    
    # Adjust list if we have fewer images than angles
    angles = angles[-len(selected_images):]

    # 4. Process Each Image
    for i, img_path in enumerate(selected_images):
        try:
            # Open and resize
            img = Image.open(img_path).convert("RGBA")
            img = ImageOps.fit(img, THUMB_SIZE, method=Image.Resampling.LANCZOS)
            
            # Create a container for this specific layer (to hold rotation + shadow)
            # Make it larger than the thumb to hold the rotation corners
            layer_size = (int(THUMB_SIZE[0] * 1.5), int(THUMB_SIZE[1] * 1.5))
            layer = Image.new('RGBA', layer_size, (0,0,0,0))
            
            # Calculate center to paste the comic
            paste_x = (layer_size[0] - THUMB_SIZE[0]) // 2
            paste_y = (layer_size[1] - THUMB_SIZE[1]) // 2
            
            # Add Drop Shadow
            # We draw a black rectangle, blur it, then paste the image on top
            shadow = Image.new('RGBA', layer_size, (0,0,0,0))
            shadow_box = (paste_x + 4, paste_y + 4, paste_x + THUMB_SIZE[0] + 4, paste_y + THUMB_SIZE[1] + 4)
            
            # Draw shadow on the shadow layer
            from PIL import ImageDraw
            d = ImageDraw.Draw(shadow)
            d.rectangle(shadow_box, fill=(0,0,0,120))
            shadow = shadow.filter(ImageFilter.GaussianBlur(radius=5))
            
            # Composite Shadow + Image onto the layer
            layer = Image.alpha_composite(layer, shadow)
            layer.paste(img, (paste_x, paste_y), img)
            
            # Rotate the layer
            angle = angles[i]
            rotated_layer = layer.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False)
            
            # Calculate final positioning on the main canvas
            # We center the layer on the canvas
            final_x = (CANVAS_SIZE[0] - rotated_layer.width) // 2
            final_y = (CANVAS_SIZE[1] - rotated_layer.height) // 2
            
            # For the "Stack" effect, we can slightly offset Y based on index
            # push back layers slightly up, front layer down
            y_offset = (i - len(selected_images)) * 5 
            
            final_canvas.paste(rotated_layer, (final_x, final_y + y_offset), rotated_layer)

        except Exception as e:
            print(f"Error processing {img_path}: {e}")

    # 5. Save
    final_canvas.save(output_path)
    print(f"Generated {output_path}")

# Usage
# create_folder_thumb("./my_comic_folder")