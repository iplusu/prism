import subprocess

def get_disk_usage(path):
    """Run the system 'df' command on the given filesystem path and return the output as a string."""
    try:
        result = subprocess.run(['df', path], capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        return f"Error: {e}"
