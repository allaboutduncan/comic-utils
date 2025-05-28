---
description: Enhance Image Quality of all images if all CBZs of a directory
icon: folder
---

# Enhance Images

<figure><img src="../../.gitbook/assets/enhance (1).png" alt=""><figcaption><p>Enhance Images</p></figcaption></figure>

Looking at scans from books that were published in decades past or by now defunct publishers, there quick and easy image enhancements that could be done to the images.

This function will run an image enhancement algorithm (documented below) on all images in a CBZ and all files in a directory. This feature does not traverse directory/sub-directories.

Below are a few _Before/After_ examples&#x20;

<table><thead><tr><th width="375" align="center">Before</th><th align="center">After</th></tr></thead><tbody><tr><td align="center"><img src="https://github.com/allaboutduncan/comic-utils/raw/main/images/enhance-before.webp" alt=""></td><td align="center"><img src="https://github.com/allaboutduncan/comic-utils/raw/main/images/enhance-after.webp" alt=""></td></tr><tr><td align="center"></td><td align="center"></td></tr></tbody></table>

For processing images, I didn't want to just simply adjust the contrast. This could have unintended consequences and make the image less legible overall. Below, I explain the logic behind the image processing and welcome any comments and refinements.

### Algorithm

The app uses the Python PIL (Pillow) library to apply an "intelligent" adjustment to each image in the CBZ.&#x20;

{% hint style="info" %}
**Analyzing Each Image**

* The function `modified_s_curve_lut()` generates a lookup table (LUT) with 256 values (one for each possible brightness level in an 8-bit image).
* The S-curve is a mathematical way of adjusting brightness—darker areas get a bit darker, mid-tones are slightly boosted, and highlights remain close to their original brightness.
* For pixels with brightness **below 128**, the full S-curve effect is applied.
* For pixels **above 128**, the function blends the original brightness with the S-curve, making the effect more subtle in brighter areas.
{% endhint %}

```python
def modified_s_curve_lut():
    lut = []
    for i in range(256):
        # Basic S-curve value using cosine.
        s = 0.5 - 0.5 * math.cos(math.pi * (i / 255))
        s_val = 255 * s
        # For values below 128, use the full S-curve.
        # For values 128 and above, blend the S-curve with the original value.
        if i < 128:
            blend = 0.0
        else:
            blend = (i - 128) / (255 - 128)  # goes from 0 at i=128 to 1 at i=255
        new_val = (1 - blend) * s_val + blend * i
        lut.append(int(round(new_val)))
    return lut

```

The code will attempt to determine if the image is grayscale, RGBA or RGB and apply the defined S-Curve accordingly

```python
def apply_modified_s_curve(image):
    single_lut = modified_s_curve_lut()
    
    # If the image is grayscale, apply the LUT directly.
    if image.mode == "L":
        return image.point(single_lut)
    # For RGB images, replicate the LUT for each channel.
    elif image.mode == "RGB":
        full_lut = single_lut * 3
        return image.point(full_lut)
    # For RGBA images, apply the curve to RGB channels only.
    elif image.mode == "RGBA":
        r, g, b, a = image.split()
        r = r.point(single_lut)
        g = g.point(single_lut)
        b = b.point(single_lut)
        return Image.merge("RGBA", (r, g, b, a))
    else:
        raise ValueError(f"Unsupported image mode: {image.mode}")
```

#### End Result

* It improves contrast by deepening shadows and slightly boosting mid-tones.
* It avoids extreme changes in bright areas, preserving detail.
* The effect is smoother and more natural compared to a standard contrast adjustment.
