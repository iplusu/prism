import requests

def fetch_remote_image(url):
    response = requests.get(url)
    response.raise_for_status()
    return response.content
