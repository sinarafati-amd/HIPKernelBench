from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime
from pathlib import Path
import os
import base64
import re
from PIL import Image
from io import BytesIO
import mimetypes

# Configs
DOC_ID = "1g_x3vTB_R1KVqkO6uR_FnFQmSYgUYhS93nqKaIU-w7c"
MD_PATH = "evalboard.md"
CREDENTIALS_PATH = "credentials.json"
MAX_URI_SIZE = 2048  # 2KB

def resize_and_encode_image(img_path: str) -> str:
    with Image.open(img_path) as img:
        img.thumbnail((600, 600))  # Resize while maintaining aspect ratio
        buffer = BytesIO()
        img_format = "JPEG" if img.mode != "RGBA" else "PNG"
        img.save(buffer, format=img_format)
        buffer.seek(0)
        return base64.b64encode(buffer.read()).decode('utf-8'), f"image/{img_format.lower()}"

def sync_to_google_docs():
    # Auth
    SCOPES = ['https://www.googleapis.com/auth/documents']
    creds = service_account.Credentials.from_service_account_file(
        CREDENTIALS_PATH, scopes=SCOPES
    )
    service = build('docs', 'v1', credentials=creds)

    # Read markdown
    with open(MD_PATH, 'r') as file:
        markdown = file.read()

    markdown += f"\n\n(Synced from Git commit on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"

    # Prepare image and text replacement
    image_pattern = re.compile(r'!\[.*?\]\((.*?)\)')
    parts = image_pattern.split(markdown)
    texts = parts[::2]
    image_paths = parts[1::2]

    requests = []

    # Fetch doc end index safely
    doc = service.documents().get(documentId=DOC_ID).execute()
    end_index = doc.get("body", {}).get("content", [])[-1].get("endIndex", 1)

    # Clear doc content
    requests.append({
        "deleteContentRange": {
            "range": {
                "startIndex": 1,
                "endIndex": end_index - 1
            }
        }
    })

    # Rebuild doc with inline images
    insert_index = 1
    for i, text in enumerate(texts):
        if text:
            requests.append({
                "insertText": {
                    "location": {"index": insert_index},
                    "text": text
                }
            })
            insert_index += len(text)

        if i < len(image_paths):
            img_path = os.path.join("HIPKernelBench", image_paths[i])
            if not os.path.exists(img_path):
                print(f"⚠️ Skipping missing image: {img_path}")
                continue

            try:
                b64_data, mime_type = resize_and_encode_image(img_path)
                uri = f"data:{mime_type};base64,{b64_data}"
                if len(uri.encode('utf-8')) > MAX_URI_SIZE:
                    raise ValueError("Image too large for inline base64 URI.")
                requests.append({
                    "insertInlineImage": {
                        "location": {"index": insert_index},
                        "uri": uri,
                        "objectSize": {"height": {"magnitude": 300, "unit": "PT"}}
                    }
                })
                insert_index += 1
            except Exception as e:
                print(f"⚠️ Failed to insert image {img_path}: {e}")
                continue

    # Sync to doc
    service.documents().batchUpdate(documentId=DOC_ID, body={"requests": requests}).execute()
    print("✅ Google Doc updated.")

if __name__ == "__main__":
    sync_to_google_docs()
