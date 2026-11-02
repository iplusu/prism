import os
import glob

def cleanup_old_logs(prefix):
    log_dir = "/var/log/myapp/"
    pattern = os.path.join(log_dir, prefix + "*")
    files_to_delete = glob.glob(pattern)
    
    for file_path in files_to_delete:
        try:
            os.remove(file_path)
        except OSError:
            pass
    
    return len(files_to_delete)
