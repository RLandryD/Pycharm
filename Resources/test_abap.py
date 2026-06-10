"""
Standalone reachability test for the trial ABAP environment.

NOTE: this file contains a LIVE client secret. Don't commit it to git or share
it; after testing you can rotate it by deleting/recreating the service key.

Run it:  python3 test_abap.py     (or the green Run button in PyCharm)
"""
import requests

# --- values taken straight from your "abap key" service key ---------------
SYSTEM_URL   = "https://e9233ee0-105b-4c71-893f-d4b6f0ddd36a.abap.us10.hana.ondemand.com"
TOKEN_URL    = "https://0140aa99trial.authentication.us10.hana.ondemand.com/oauth/token"
CLIENT_ID    = "sb-5ee23051-62e3-44cd-afa6-14aa7c3f2d0b!b655221|abap-trial-service-broker!b3132"
CLIENT_SECRET = "db8183f4-5550-46a0-a485-42f3a3e90045$pub_E0XMiv322mFaPjMYQrpcqhOstfmOVxgwHiQnDS8="
CATALOG_PATH = "/sap/opu/odata/IWFND/CATALOGSERVICE;v=2/ServiceCollection?$top=5"
# --------------------------------------------------------------------------


def main():
    print("token endpoint:", TOKEN_URL)
    tr = requests.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(CLIENT_ID, CLIENT_SECRET),
        headers={"Accept": "application/json"},
        timeout=30,
    )
    print("token request HTTP:", tr.status_code)
    if tr.status_code != 200:
        print("token error body:", tr.text[:400])
        return

    access = tr.json().get("access_token", "")
    print("token length:", len(access))
    if not access:
        print("no access_token in response:", tr.text[:400])
        return

    url = SYSTEM_URL.rstrip("/") + CATALOG_PATH
    print("\ncalling catalog:", url)
    r = requests.get(
        url,
        headers={"Authorization": "Bearer " + access, "Accept": "application/json"},
        timeout=30,
    )
    print("catalog HTTP:", r.status_code)
    print("body (first 600 chars):")
    print(r.text[:600])


if __name__ == "__main__":
    main()
