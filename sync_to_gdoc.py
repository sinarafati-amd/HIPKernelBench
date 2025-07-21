import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime
import base64
import os
import mimetypes

DOC_ID = "1g_x3vTB_R1KVqkO6uR_FnFQmSYgUYhS93nqKaIU-w7c"
MD_PATH = "evalboard.md"
CREDENTIALS_PATH = "credentials.json"

def sync_to_google_docs():
    SCOPES = ['https://www.googleapis.com/auth/documents']
    creds = service_account.Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
    service = build('docs', 'v1', credentials=creds)

    with open(MD_PATH, 'r') as file:
        markdown_lines = file.readlines()

    doc = service.documents().get(documentId=DOC_ID).execute()
    end_index = doc.get("body", {}).get("content", [])[-1].get("endIndex", 1)

    requests = [
        {
            "deleteContentRange": {
                "range": {
                    "startIndex": 1,
                    "endIndex": end_index - 1
                }
            }
        }
    ]

    # Process each line: embed images, preserve text
    for line in reversed(markdown_lines):  
        match = re.match(r'!\[(.*?)\]\((.*?)\)', line.strip())
        if match:
            alt_text, img_path = match.groups()
            if os.path.exists(img_path):
                mime_type, _ = mimetypes.guess_type(img_path)
                with open(img_path, 'rb') as img_file:
                    image_data = base64.b64encode(img_file.read()).decode('utf-8')

                requests.insert(1, {
                    "insertInlineImage": {
                        "location": {"index": 1},
                        "uri": f"data:{mime_type};base64,{image_data}",
                        "objectSize": {"height": {"magnitude": 300, "unit": "PT"}}
                    }
                })
                requests.insert(1, {
                    "insertText": {"location": {"index": 1}, "text": f"{alt_text}\n"}
                })
            else:
                requests.insert(1, {
                    "insertText": {"location": {"index": 1}, "text": f"[Image not found: {img_path}]\n"}
                })
        else:
            requests.insert(1, {
                "insertText": {"location": {"index": 1}, "text": line}
            })

    # Add timestamp
    timestamp = f"\n(Synced from Git commit on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')})\n"
    requests.insert(1, {"insertText": {"location": {"index": 1}, "text": timestamp}})

    service.documents().batchUpdate(documentId=DOC_ID, body={"requests": requests}).execute()
    print("✅ Google Doc updated.")

if __name__ == "__main__":
    sync_to_google_docs()
