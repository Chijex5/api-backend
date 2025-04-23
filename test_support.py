import requests

url = "http://localhost:5000/support"

payload = {
    "identifier": "embroconnect3@gmail.com",
    "message": "I paid but your website says I haven’t yet paid"
}

response = requests.post(url, json=payload)

print("Status:", response.status_code)
print("Response:", response.json())
