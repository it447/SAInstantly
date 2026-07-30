import os

import requests

API_BASE = "https://api.hubapi.com"
CONTACTS_OBJECT_TYPE_ID = "0-1"


def get_api_key():
    """The HubSpot private-app API key, configured only via the
    HUBSPOT_API_KEY environment variable (Vercel project settings) -- never
    entered through the UI or stored in Redis."""
    return os.environ.get("HUBSPOT_API_KEY", "").strip()


def _headers(api_key):
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def get_list(api_key, list_id):
    resp = requests.get(f"{API_BASE}/crm/v3/lists/{list_id}", headers=_headers(api_key), timeout=15)
    resp.raise_for_status()
    return resp.json()


def list_all_lists(api_key, count=200):
    """Returns every contact list as [{id, name}], for populating the list picker."""
    all_lists = []
    offset = 0
    while True:
        resp = requests.post(
            f"{API_BASE}/crm/v3/lists/search",
            headers=_headers(api_key),
            json={"objectTypeId": CONTACTS_OBJECT_TYPE_ID, "count": count, "offset": offset},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        for hs_list in data.get("lists", []):
            list_id = hs_list.get("listId")
            all_lists.append({"id": str(list_id), "name": hs_list.get("name") or f"List {list_id}"})
        if not data.get("hasMore"):
            break
        offset += count
    all_lists.sort(key=lambda l: l["name"].lower())
    return all_lists


def get_list_member_ids(api_key, list_id, after=None, count=100):
    """Returns (record_ids, next_after)."""
    params = {"count": count}
    if after:
        params["after"] = after
    resp = requests.get(
        f"{API_BASE}/crm/v3/lists/{list_id}/memberships/join-order",
        headers=_headers(api_key),
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    record_ids = [str(r["recordId"]) for r in data.get("results", [])]
    next_after = data.get("paging", {}).get("next", {}).get("after")
    return record_ids, next_after


def get_all_list_member_ids(api_key, list_id):
    all_ids = []
    after = None
    while True:
        ids, after = get_list_member_ids(api_key, list_id, after=after)
        all_ids.extend(ids)
        if not after:
            break
    return all_ids


DEFAULT_PROPERTIES = ("email", "firstname", "lastname", "company")


def batch_read_contacts(api_key, contact_ids, properties=DEFAULT_PROPERTIES):
    """Returns a list of {email, hubspot_id, properties: {name: value}} using
    HubSpot's own internal property names (e.g. "firstname", not
    "first_name"), so merge tags typed as {{firstname}} line up exactly with
    what a user picks from the contact-properties list.
    """
    if not contact_ids:
        return []
    properties = list(dict.fromkeys(list(properties) + ["email"]))  # email is always required, no dupes
    resp = requests.post(
        f"{API_BASE}/crm/v3/objects/contacts/batch/read",
        headers=_headers(api_key),
        json={
            "properties": properties,
            "inputs": [{"id": cid} for cid in contact_ids],
        },
        timeout=20,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    contacts = []
    for r in results:
        props = r.get("properties", {})
        email = props.get("email")
        if not email:
            continue
        contacts.append(
            {
                "email": email.strip().lower(),
                "hubspot_id": r.get("id"),
                "properties": {name: (props.get(name) or "") for name in properties},
            }
        )
    return contacts


def get_contact_properties(api_key):
    """Returns the list of contact properties available for personalization:
    [{name, label, group_name}], skipping hidden/calculated/read-only ones
    that wouldn't make sense as merge tags.
    """
    resp = requests.get(
        f"{API_BASE}/crm/v3/properties/contacts",
        headers=_headers(api_key),
        timeout=15,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    properties = []
    for p in results:
        if p.get("hidden") or p.get("calculated"):
            continue
        properties.append(
            {
                "name": p["name"],
                "label": p.get("label") or p["name"],
                "group_name": p.get("groupName") or "",
            }
        )
    properties.sort(key=lambda p: (p["group_name"], p["label"]))
    return properties
