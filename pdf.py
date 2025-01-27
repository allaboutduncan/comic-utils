import os
import sys
import zipfile
from pdf2image import convert_from_path

def scan_and_convert(directory):
    """
    Recursively scans a directory for PDF files, converts each PDF's pages to images, 
    organizes them into folders, and creates a CBZ file for each PDF.

    :param directory: Root directory to scan
    """
    print(f"Scanning directory: {directory}")
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith('.pdf'):
                pdf_path = os.path.join(root, file)
                pdf_name = os.path.splitext(file)[0]

                # Create a folder for the PDF
                output_folder = os.path.join(root, pdf_name)
                os.makedirs(output_folder, exist_ok=True)

                # Convert PDF pages to images
                print(f"Processing: {pdf_path}")
                try:
                    pages = convert_from_path(pdf_path)
                    for i, page in enumerate(pages):
                        page_filename = f"{pdf_name} page_{i + 1}.jpg"
                        page_path = os.path.join(output_folder, page_filename)
                        page.save(page_path, 'JPEG')

                    # Compress the folder into a CBZ file
                    cbz_path = os.path.join(root, f"{pdf_name}.cbz")
                    with zipfile.ZipFile(cbz_path, 'w', zipfile.ZIP_DEFLATED) as cbz:
                        for folder_root, _, folder_files in os.walk(output_folder):
                            for folder_file in folder_files:
                                file_path = os.path.join(folder_root, folder_file)
                                arcname = os.path.relpath(file_path, output_folder)
                                cbz.write(file_path, arcname)

                    print(f"CBZ file created: {cbz_path}")

                except Exception as e:
                    print(f"Error processing {pdf_path}: {e}")

                # Clean up by deleting the extracted folder (optional)
                for folder_root, _, folder_files in os.walk(output_folder):
                    for folder_file in folder_files:
                        os.remove(os.path.join(folder_root, folder_file))
                os.rmdir(output_folder)

if __name__ == "__main__":
    # The directory path is passed as the first argument
    if len(sys.argv) < 2:
        print("No directory provided!")
    else:
        directory = sys.argv[1]
        scan_and_convert(directory)