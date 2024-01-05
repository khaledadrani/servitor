import requests

def test_asgi_server():
    url = 'http://localhost:8044/'

    response = requests.get(url)

    print(f'Status Code: {response.status_code}')
    print('Response Body:')
    print(response.text)

if __name__ == "__main__":
    test_asgi_server()
