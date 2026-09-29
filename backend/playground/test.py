# import requests

# response=requests.get("https://jsonplaceholder.typicode.com/posts/1")
# print("Status:",response.status_code)

# data=response.json()

# print("User ID:",data["userId"])
# print("ID:",data["id"])
# print("Title:",data["title"])
# print("Body:",data["body"])

import requests

ticket={
    "title":"testing ticket",
    "description":"testing ticket description"
}

response=requests.post("https://jsonplaceholder.typicode.com/posts",json=ticket)

print("Status:",response.status_code)

data=response.json()

print("Title:",data["title"])
print("Description:",data["description"])