import os

def delete_log_file(filename):
    file_path = os.path.join("/var/log/myapp/", filename)
    if os.path.exists(file_path):
        os.remove(file_path)
        return True
    return False
