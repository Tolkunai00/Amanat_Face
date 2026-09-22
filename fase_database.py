"""Backward-compatible entry point for the photo import utility.

The original filename is preserved so existing commands keep working.
"""

from app.database.face_database import import_photos_to_database


if __name__ == "__main__":
    import_photos_to_database()
