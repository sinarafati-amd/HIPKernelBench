from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.http import MediaFileUpload
from googleapiclient.discovery import build
from datetime import datetime
import json
import os
import re

# === CONFIG ===
DOC_ID = "1g_x3vTB_R1KVqkO6uR_FnFQmSYgUYhS93nqKaIU-w7c"
MD_PATH = "evalboard.md"
CLIENT_SECRET_FILE = "client_secret.json"
SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file"
]

# === AUTH ===
def get_oauth_credentials():
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
    creds = flow.run_local_server(port=0)
    return creds

# === DRIVE UPLOAD ===

CACHE_FILE = ".gdoc_image_cache.json"

# Load cache if exists
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "r") as f:
        IMAGE_CACHE = json.load(f)
else:
    IMAGE_CACHE = {}

def upload_image_to_drive(creds, image_path):
    global IMAGE_CACHE

    if image_path in IMAGE_CACHE:
        return f"https://drive.google.com/uc?id={IMAGE_CACHE[image_path]}"

    drive_service = build("drive", "v3", credentials=creds)
    file_metadata = {
        "name": os.path.basename(image_path),
        "mimeType": "image/png"
    }
    media = MediaFileUpload(image_path, mimetype="image/png")
    file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id"
    ).execute()

    drive_service.permissions().create(
        fileId=file["id"],
        body={"type": "anyone", "role": "reader"}
    ).execute()

    # Save to cache
    IMAGE_CACHE[image_path] = file["id"]
    with open(CACHE_FILE, "w") as f:
        json.dump(IMAGE_CACHE, f)

    return f"https://drive.google.com/uc?id={file['id']}"

# === PARSE MARKDOWN LINES TO FORMATTED INSERTS ===
def parse_markdown_line(line, insert_index):
    requests = []
    style_requests = []
    original_line = line
    line += "\n"
    start = insert_index
    end = insert_index + len(line)

    # Headings
    heading_level = 0
    if line.startswith("# "):
        heading_level = 1
        text = line[2:]
    elif line.startswith("## "):
        heading_level = 2
        text = line[3:]
    elif line.startswith("### "):
        heading_level = 3
        text = line[4:]
    else:
        text = line

    # Clean bold/italic
    bold_spans = [(m.start(1), m.end(1)) for m in re.finditer(r'\*\*(.*?)\*\*', text)]
    italic_spans = [(m.start(1), m.end(1)) for m in re.finditer(r'\*(.*?)\*', text) if not m.group(0).startswith("**")]

    # Remove markdown symbols
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)

    # Insert cleaned text
    requests.append({
        "insertText": {
            "location": {"index": insert_index},
            "text": text
        }
    })

    # Heading styling
    if heading_level > 0:
        style_requests.append({
            "updateTextStyle": {
                "range": {"startIndex": start, "endIndex": end - 1},
                "textStyle": {
                    "bold": True,
                    "fontSize": {"magnitude": 18 - (heading_level * 2), "unit": "PT"}
                },
                "fields": "bold,fontSize"
            }
        })

    # Bold styling
    offset = 0
    for s, e in bold_spans:
        style_requests.append({
            "updateTextStyle": {
                "range": {
                    "startIndex": start + s - offset,
                    "endIndex": start + e - offset
                },
                "textStyle": {"bold": True},
                "fields": "bold"
            }
        })

    # Italic styling
    for s, e in italic_spans:
        style_requests.append({
            "updateTextStyle": {
                "range": {
                    "startIndex": start + s,
                    "endIndex": start + e
                },
                "textStyle": {"italic": True},
                "fields": "italic"
            }
        })

    return requests, style_requests, len(text)

# === MAIN FUNCTION ===
def sync_to_google_docs():
    creds = get_oauth_credentials()
    docs_service = build("docs", "v1", credentials=creds)

    with open(MD_PATH, 'r') as file:
        markdown = file.read()

    markdown += f"\n\n(Synced from Git commit on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"

    image_pattern = re.compile(r'!\[.*?\]\((.*?)\)')
    parts = image_pattern.split(markdown)
    texts = parts[::2]
    image_paths = parts[1::2]

    requests = []

    # Clear existing content
    doc = docs_service.documents().get(documentId=DOC_ID).execute()
    end_index = doc.get("body", {}).get("content", [])[-1].get("endIndex", 1)
    requests.append({
        "deleteContentRange": {
            "range": {"startIndex": 1, "endIndex": end_index - 1}
        }
    })

    insert_index = 1
    for i, text_block in enumerate(texts):
        style_requests = []
        for line in text_block.splitlines():
            text_reqs, style_reqs, added_len = parse_markdown_line(line, insert_index)
            requests.extend(text_reqs)
            requests.extend(style_reqs)
            insert_index += added_len

        if i < len(image_paths):
            img_path = os.path.abspath(image_paths[i])
            if not os.path.exists(img_path):
                print(f"⚠️ Skipping missing image: {img_path}")
                continue
            try:
                image_url = upload_image_to_drive(creds, img_path)
                requests.append({
                    "insertInlineImage": {
                        "location": {"index": insert_index},
                        "uri": image_url,
                        "objectSize": {
                            "height": {"magnitude": 300, "unit": "PT"},
                            "width": {"magnitude": 400, "unit": "PT"}
                        }
                    }
                })
                insert_index += 1
            except Exception as e:
                print(f"⚠️ Failed to insert image {img_path}: {e}")
                continue

    docs_service.documents().batchUpdate(documentId=DOC_ID, body={"requests": requests}).execute()
    print("✅ Google Doc updated with markdown formatting.")

# === RUN ===
if __name__ == "__main__":
    sync_to_google_docs()
