import requests
import os

# ====== CONFIG ======
API_KEY = "6c046fbd-e4a5-42b0-a73e-45d8097c7305"  # Hardcoded for testing
# ====================

def pixeldrain_download(file_id):
    file_id = input("Enter Pixeldrain file ID: ").strip()

    # Pixeldrain API endpoint with download flag
    url = f"https://pixeldrain.com/api/file/{file_id}"

    # HTTP Basic Auth: username empty, password = API_KEY
    auth = ("", API_KEY)

    print(f"Downloading file {file_id}...")
    response = requests.get(url, auth=auth, stream=True)

    # Check status
    if response.status_code != 200:
        print(f"Error: {response.status_code} - {response.text}")
        return

    # Try to get filename from headers, fallback to file_id
    content_disp = response.headers.get("Content-Disposition", "")
    filename = file_id
    if "filename=" in content_disp:
        filename = content_disp.split("filename=")[-1].strip('"')

    # Save file
    with open(filename, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    print(f"✅ File saved as: {filename}")

if __name__ == "__main__":
    pixeldrain_download()
