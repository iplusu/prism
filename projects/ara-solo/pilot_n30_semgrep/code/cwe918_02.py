import requests

def proxy_download(url, out_path):
    response = requests.get(url)
    response.raise_for_status()
    
    with open(out_path, 'wb') as f:
        f.write(response.content)
    
    return len(response.content)
