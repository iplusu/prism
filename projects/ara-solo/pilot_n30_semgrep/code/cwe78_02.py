import subprocess
import os

def count_total_lines(dirname):
    """Count total lines across all text files in the directory using shell command."""
    if not os.path.exists(dirname):
        return 0
    
    # Use find to locate all files and wc to count lines
    cmd = ['find', dirname, '-type', 'f', '-exec', 'wc', '-l', '{}', '+']
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        # The last line of wc output contains the total count
        lines = result.stdout.strip().split('\n')
        if lines and lines[-1].startswith('total'):
            return int(lines[-1].split()[0])
        elif lines and lines[-1].split():
            return int(lines[-1].split()[0])
        return 0
    except (subprocess.CalledProcessError, ValueError, IndexError):
        return 0
