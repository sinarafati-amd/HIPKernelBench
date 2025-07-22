import json
import os
import re
from datetime import datetime
from typing import List, Tuple, Dict, Any

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


class Config:
    """Configuration settings for the sync tool."""
    DOC_ID = "1g_x3vTB_R1KVqkO6uR_FnFQmSYgUYhS93nqKaIU-w7c"
    MD_PATH = "evalboard.md"
    CLIENT_SECRET_FILE = "client_secret.json"
    CACHE_FILE = ".gdoc_image_cache.json"
    SCOPES = [
        "https://www.googleapis.com/auth/documents",
        "https://www.googleapis.com/auth/drive.file"
    ]


class ImageCache:
    """Handles caching of uploaded images to avoid re-uploads."""
    
    def __init__(self, cache_file: str):
        self.cache_file = cache_file
        self.cache = self._load_cache()
    
    def _load_cache(self) -> Dict[str, str]:
        """Load existing cache from file."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                print(f"⚠️  Cache file corrupted, starting fresh")
        return {}
    
    def get(self, image_path: str) -> str | None:
        """Get cached Drive file ID for image path."""
        return self.cache.get(image_path)
    
    def set(self, image_path: str, file_id: str) -> None:
        """Cache a Drive file ID for an image path."""
        self.cache[image_path] = file_id
        self._save_cache()
    
    def _save_cache(self) -> None:
        """Save cache to file."""
        try:
            with open(self.cache_file, "w") as f:
                json.dump(self.cache, f, indent=2)
        except IOError as e:
            print(f"⚠️  Failed to save cache: {e}")


class DriveUploader:
    """Handles uploading images to Google Drive."""
    
    def __init__(self, credentials, cache: ImageCache):
        self.drive_service = build("drive", "v3", credentials=credentials)
        self.cache = cache
    
    def upload_image(self, image_path: str) -> str:
        """Upload an image to Drive and return its public URL."""
        # Check cache first
        cached_id = self.cache.get(image_path)
        if cached_id:
            return f"https://drive.google.com/uc?id={cached_id}"
        
        # Upload new image
        try:
            file_metadata = {
                "name": os.path.basename(image_path),
                "mimeType": "image/png"
            }
            media = MediaFileUpload(image_path, mimetype="image/png")
            
            file = self.drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id"
            ).execute()
            
            # Make file publicly readable
            self.drive_service.permissions().create(
                fileId=file["id"],
                body={"type": "anyone", "role": "reader"}
            ).execute()
            
            # Cache the result
            file_id = file["id"]
            self.cache.set(image_path, file_id)
            
            return f"https://drive.google.com/uc?id={file_id}"
            
        except Exception as e:
            raise Exception(f"Failed to upload {image_path}: {e}")


class MarkdownParser:
    """Parses Markdown and converts to Google Docs API requests."""
    
    @staticmethod
    def parse_line(line: str, insert_index: int) -> Tuple[List[Dict], int]:
        """Parse a single line of Markdown and return API requests."""
        if not line.strip():
            # Insert empty line to preserve spacing
            return [{
                "insertText": {
                    "location": {"index": insert_index},
                    "text": "\n"
                }
            }], 1
            
        requests = []
        original_line = line
        
        # Determine line type and extract text
        text, line_type = MarkdownParser._extract_text_and_type(line)
        
        # Handle bold and italic formatting
        formatted_text, style_requests = MarkdownParser._process_formatting(
            text, insert_index
        )
        
        # Insert the text
        full_text = formatted_text + "\n"
        requests.append({
            "insertText": {
                "location": {"index": insert_index},
                "text": full_text
            }
        })
        
        text_end = insert_index + len(formatted_text)
        
        # Apply paragraph-level styling
        paragraph_request = MarkdownParser._get_paragraph_style(
            line_type, insert_index, text_end
        )
        if paragraph_request:
            requests.append(paragraph_request)
        
        # Apply character-level styling (bold/italic)
        requests.extend(style_requests)
        
        return requests, len(full_text)
    
    @staticmethod
    def _extract_text_and_type(line: str) -> Tuple[str, str]:
        """Extract clean text and determine line type."""
        line = line.strip()
        
        if line.startswith("# "):
            return line[2:].strip(), "h1"
        elif line.startswith("## "):
            return line[3:].strip(), "h2"
        elif line.startswith("### "):
            return line[4:].strip(), "h3"
        elif line.startswith("#### "):
            return line[5:].strip(), "h4"
        elif line.startswith("- ") or line.startswith("* "):
            return line[2:].strip(), "bullet"
        elif re.match(r'^\d+\.\s', line):
            return re.sub(r'^\d+\.\s', '', line).strip(), "number"
        else:
            return line, "normal"
    
    @staticmethod
    def _process_formatting(text: str, start_index: int) -> Tuple[str, List[Dict]]:
        """Process bold and italic formatting, return clean text and style requests."""
        style_requests = []
        
        # Find bold patterns (** or __)
        bold_matches = list(re.finditer(r'\*\*(.*?)\*\*|__(.*?)__', text))
        # Find italic patterns (* or _) that aren't part of bold
        italic_matches = list(re.finditer(r'(?<!\*)\*([^*]+?)\*(?!\*)|(?<!_)_([^_]+?)_(?!_)', text))
        
        # Sort all matches by position for proper offset calculation
        all_matches = []
        for match in bold_matches:
            content = match.group(1) or match.group(2)
            all_matches.append({
                'start': match.start(),
                'end': match.end(),
                'content': content,
                'type': 'bold',
                'marker_len': len(match.group(0)) - len(content)
            })
        
        for match in italic_matches:
            content = match.group(1) or match.group(2)
            all_matches.append({
                'start': match.start(),
                'end': match.end(),
                'content': content,
                'type': 'italic',
                'marker_len': len(match.group(0)) - len(content)
            })
        
        all_matches.sort(key=lambda x: x['start'])
        
        # Remove markdown markers and calculate style positions
        clean_text = text
        offset = 0
        
        for match in all_matches:
            # Adjust positions for previous removals
            adjusted_start = match['start'] - offset
            adjusted_end = match['end'] - offset
            
            # Remove markdown markers
            if match['type'] == 'bold':
                clean_text = clean_text[:adjusted_start] + match['content'] + clean_text[adjusted_end:]
            else:  # italic
                clean_text = clean_text[:adjusted_start] + match['content'] + clean_text[adjusted_end:]
            
            # Create style request
            content_start = start_index + adjusted_start
            content_end = content_start + len(match['content'])
            
            style_requests.append({
                "updateTextStyle": {
                    "range": {
                        "startIndex": content_start,
                        "endIndex": content_end
                    },
                    "textStyle": {match['type']: True},
                    "fields": match['type']
                }
            })
            
            # Update offset for next iteration
            offset += match['marker_len']
        
        return clean_text, style_requests
    
    @staticmethod
    def _get_paragraph_style(line_type: str, start_index: int, end_index: int) -> Dict | None:
        """Get paragraph style request for a line type."""
        if line_type == "h1":
            return {
                "updateParagraphStyle": {
                    "range": {"startIndex": start_index, "endIndex": end_index},
                    "paragraphStyle": {"namedStyleType": "HEADING_1"},
                    "fields": "namedStyleType"
                }
            }
        elif line_type == "h2":
            return {
                "updateParagraphStyle": {
                    "range": {"startIndex": start_index, "endIndex": end_index},
                    "paragraphStyle": {"namedStyleType": "HEADING_2"},
                    "fields": "namedStyleType"
                }
            }
        elif line_type == "h3":
            return {
                "updateParagraphStyle": {
                    "range": {"startIndex": start_index, "endIndex": end_index},
                    "paragraphStyle": {"namedStyleType": "HEADING_3"},
                    "fields": "namedStyleType"
                }
            }
        elif line_type == "h4":
            return {
                "updateParagraphStyle": {
                    "range": {"startIndex": start_index, "endIndex": end_index},
                    "paragraphStyle": {"namedStyleType": "HEADING_4"},
                    "fields": "namedStyleType"
                }
            }
        elif line_type in ["bullet", "number"]:
            preset = "BULLET_DISC_CIRCLE_SQUARE" if line_type == "bullet" else "NUMBERED_DECIMAL_ALPHA_ROMAN"
            return {
                "createParagraphBullets": {
                    "range": {"startIndex": start_index, "endIndex": end_index},
                    "bulletPreset": preset
                }
            }
        
        return None


class GoogleDocsSync:
    """Main class for syncing Markdown to Google Docs."""
    
    def __init__(self, config: Config):
        self.config = config
        self.credentials = self._get_credentials()
        self.docs_service = build("docs", "v1", credentials=self.credentials)
        self.image_cache = ImageCache(config.CACHE_FILE)
        self.uploader = DriveUploader(self.credentials, self.image_cache)
    
    def _get_credentials(self):
        """Get OAuth2 credentials."""
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                self.config.CLIENT_SECRET_FILE, 
                self.config.SCOPES
            )
            return flow.run_local_server(port=0)
        except Exception as e:
            raise Exception(f"Authentication failed: {e}")
    
    def sync(self) -> None:
        """Main sync function."""
        print("📄 Reading Markdown file...")
        try:
            with open(self.config.MD_PATH, 'r', encoding='utf-8') as file:
                markdown_content = file.read()
        except FileNotFoundError:
            raise Exception(f"Markdown file not found: {self.config.MD_PATH}")
        
        # Add sync timestamp
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        markdown_content += f"\n\n*Synced from Markdown on {timestamp}*"
        
        print("🔄 Converting Markdown to Google Docs requests...")
        requests = self._convert_markdown_to_requests(markdown_content)
        
        print("📝 Updating Google Doc...")
        try:
            # Clear existing content and apply new content in batch
            self.docs_service.documents().batchUpdate(
                documentId=self.config.DOC_ID,
                body={"requests": requests}
            ).execute()
            
            print("✅ Successfully synced to Google Docs!")
            print(f"🔗 View document: https://docs.google.com/document/d/{self.config.DOC_ID}")
            
        except Exception as e:
            raise Exception(f"Failed to update Google Doc: {e}")
    
    def _convert_markdown_to_requests(self, markdown: str) -> List[Dict]:
        """Convert Markdown content to Google Docs API requests."""
        requests = []
        
        # Clear existing content first
        try:
            doc = self.docs_service.documents().get(documentId=self.config.DOC_ID).execute()
            end_index = doc.get("body", {}).get("content", [])[-1].get("endIndex", 1)
            if end_index > 1:
                requests.append({
                    "deleteContentRange": {
                        "range": {"startIndex": 1, "endIndex": end_index - 1}
                    }
                })
        except Exception as e:
            print(f"⚠️  Warning: Could not clear existing content: {e}")
        
        # Handle images and text blocks
        image_pattern = re.compile(r'!\[.*?\]\((.*?)\)')
        parts = image_pattern.split(markdown)
        text_blocks = parts[::2]  # Even indices are text
        image_paths = parts[1::2]  # Odd indices are image paths
        
        insert_index = 1
        
        for i, text_block in enumerate(text_blocks):
            # Process text block line by line
            if text_block.strip():
                lines = text_block.splitlines()
                for j, line in enumerate(lines):
                    line_requests, added_length = MarkdownParser.parse_line(line, insert_index)
                    requests.extend(line_requests)
                    insert_index += added_length
                
                # Add extra spacing after text blocks if they're not empty
                if lines:
                    requests.append({
                        "insertText": {
                            "location": {"index": insert_index},
                            "text": "\n"
                        }
                    })
                    insert_index += 1
            
            # Handle image if present
            if i < len(image_paths):
                image_path = os.path.abspath(image_paths[i].strip())
                if os.path.exists(image_path):
                    try:
                        print(f"📸 Uploading image: {os.path.basename(image_path)}")
                        image_url = self.uploader.upload_image(image_path)
                        
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
                        insert_index += 1  # Images take up 1 character position
                        
                    except Exception as e:
                        print(f"⚠️  Failed to upload image {image_path}: {e}")
                else:
                    print(f"⚠️  Image not found: {image_path}")
        
        return requests


def main():
    """Main entry point."""
    try:
        config = Config()
        syncer = GoogleDocsSync(config)
        syncer.sync()
    except Exception as e:
        print(f"❌ Error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    exit(main())