from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime
import os

DOC_ID = "1g_x3vTB_R1KVqkO6uR_FnFQmSYgUYhS93nqKaIU-w7c"
MD_PATH = "evalboard.md"
CREDENTIALS_PATH = "credentials.json"

def sync_to_google_docs():
    SCOPES = ['https://www.googleapis.com/auth/documents']
    creds = service_account.Credentials.from_service_account_file(
        CREDENTIALS_PATH, scopes=SCOPES
    )

    service = build('docs', 'v1', credentials=creds)

    with open(MD_PATH, 'r') as file:
        content = file.read()

    content += f"\n\n(Synced from Git commit on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"

    # Get current document to find actual end index
    doc = service.documents().get(documentId=DOC_ID).execute()
    end_index = doc.get("body", {}).get("content", [])[-1].get("endIndex", 1)

    requests = [
        {
            "deleteContentRange": {
                "range": {
                    "startIndex": 1,
                    "endIndex": end_index - 1  # safe: stays within bounds
                }
            }
        },
        {
            "insertText": {
                "location": {"index": 1},
                "text": content
            }
        }
    ]

    service.documents().batchUpdate(documentId=DOC_ID, body={"requests": requests}).execute()
    print("✅ Google Doc updated.")

if __name__ == "__main__":
    sync_to_google_docs()
