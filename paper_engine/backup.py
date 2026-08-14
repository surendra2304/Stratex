import os
import shutil
import time

class BackupManager:
    """Creates rolling backups of critical state files."""
    def __init__(self, backup_dir="backups", max_backups=5):
        self.backup_dir = backup_dir
        self.max_backups = max_backups
        
        if not os.path.exists(self.backup_dir):
            os.makedirs(self.backup_dir, exist_ok=True)
            
    def backup_files(self, files_to_backup):
        """Creates a timestamped backup directory and copies files into it."""
        ts = int(time.time())
        current_backup_dir = os.path.join(self.backup_dir, f"backup_{ts}")
        
        os.makedirs(current_backup_dir, exist_ok=True)
        
        for file in files_to_backup:
            if os.path.exists(file):
                try:
                    shutil.copy2(file, os.path.join(current_backup_dir, os.path.basename(file)))
                except Exception as e:
                    from paper_engine.exceptions import PersistenceError
                    raise PersistenceError(f"Backup failed for {file}: {e}")
                    
        self._enforce_retention()
        return current_backup_dir
        
    def _enforce_retention(self):
        """Deletes older backups to stay within max_backups limit."""
        backups = []
        for entry in os.listdir(self.backup_dir):
            if entry.startswith("backup_"):
                full_path = os.path.join(self.backup_dir, entry)
                if os.path.isdir(full_path):
                    backups.append(full_path)
                    
        # Sort by creation time
        backups.sort(key=os.path.getctime)
        
        while len(backups) > self.max_backups:
            oldest = backups.pop(0)
            shutil.rmtree(oldest, ignore_errors=True)
