import requests
import json

URL = "https://www.landmarkcinemas.com/Umbraco/Api/SeatMapApi/GetSessionSeatMap"

headers = {
    "authority": "www.landmarkcinemas.com",
    "accept": "application/json, text/javascript, */*; q=0.01",
    "accept-language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
    "cache-control": "no-cache",
    "content-type": "application/json; charset=UTF-8",
    "origin": "https://www.landmarkcinemas.com",
    "pragma": "no-cache",
    "referer": "https://www.landmarkcinemas.com/",
    "sec-ch-ua": '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/152.0.0.0 Safari/537.36"
    ),
    "x-requested-with": "XMLHttpRequest",
    "x-xsrf-token": (
        "VPUlGSlruWIEfK_Z9iMIhaUqRh693zA7ZdhulWRwAq8Sn6EcFkg7I--"
        "BqyX5QW4eIol79gWhFsATMYUlHbzJMf0oX7h35BjPIJH0HHDhCQg1:"
        "fpp9Qv7vmvjAyb2JtZ9f4JvsWJalpeIlJnugfqoaox4n_KnS4WEAzjtr"
        "2EkUyaepRut5voMK8WZKUPqlaqlyMZ8tqRvyolx3zGejy_sfQd3rlmL2"
        "eqKcQcAmUkLvLOf29WKyhK51q9ZgFfURXxQmOg2"
    )
}

payload = {
    "SessionId": 11555649,
    "CinemaId": 184
}


print("=" * 70)
print("LANDMARK SEAT MAP TEST")
print("=" * 70)

print("\nURL:")
print(URL)

print("\nPayload:")
print(json.dumps(payload, indent=2))

print("\nSending POST request...\n")


try:

    response = requests.post(
        URL,
        headers=headers,
        json=payload,
        timeout=30
    )

    print("HTTP STATUS:", response.status_code)

    print(
        "CONTENT TYPE:",
        response.headers.get("content-type")
    )

    print(
        "RESPONSE SIZE:",
        len(response.content),
        "bytes"
    )

    print("\n" + "=" * 70)
    print("RESPONSE")
    print("=" * 70)

    if not response.text.strip():

        print("\nEMPTY RESPONSE")

    else:

        try:

            data = response.json()

            print(
                json.dumps(
                    data,
                    indent=2,
                    ensure_ascii=False
                )
            )

        except requests.exceptions.JSONDecodeError:

            print("\nResponse is not JSON:\n")
            print(response.text)


except requests.RequestException as e:

    print("\nREQUEST ERROR:")
    print(e)


print("\n" + "=" * 70)
print("DONE")
print("=" * 70)
