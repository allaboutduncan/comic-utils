import os
import zipfile
import shutil
import xml.etree.ElementTree as ET
from tempfile import NamedTemporaryFile

def update_volume_in_cbz(folder_path: str, volume_value: str):
    if not os.path.isdir(folder_path):
        print(f"Error: {folder_path} is not a valid directory.")
        return

    for filename in os.listdir(folder_path):
        if not filename.lower().endswith(".cbz"):
            continue

        cbz_path = os.path.join(folder_path, filename)
        print(f"Processing: {filename}...")

        try:
            with zipfile.ZipFile(cbz_path, "r") as zf:
                if "ComicInfo.xml" not in zf.namelist():
                    print(f"  [Skipped] ComicInfo.xml not found.")
                    continue
                
                xml_data = zf.read("ComicInfo.xml")

            # Parse and Check if change is needed
            root = ET.fromstring(xml_data)
            volume_elem = root.find("Volume")
            
            if volume_elem is None:
                volume_elem = ET.SubElement(root, "Volume")
            
            # Optimization: Skip write if value is already correct
            if volume_elem.text == str(volume_value):
                print(f"  [Skipped] Volume is already {volume_value}.")
                continue

            volume_elem.text = str(volume_value)
            new_xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)

            # Create a temp file in the same directory as the source
            # This ensures os.replace works across different partitions
            with NamedTemporaryFile(dir=folder_path, delete=False) as tmp_file:
                temp_path = tmp_file.name

            with zipfile.ZipFile(cbz_path, "r") as zf_in, \
                 zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf_out:
                
                for item in zf_in.infolist():
                    if item.filename == "ComicInfo.xml":
                        zf_out.writestr(item, new_xml_bytes)
                    else:
                        # Stream the data to keep memory usage low
                        with zf_in.open(item.filename) as source, \
                             zf_out.open(item, "w") as target:
                            shutil.copyfileobj(source, target)

            os.replace(temp_path, cbz_path)
            print("  [Updated] Volume set.")

        except Exception as e:
            print(f"  [Error] Failed to process {filename}: {e}")
            if 'temp_path' in locals() and os.path.exists(temp_path):
                os.remove(temp_path)