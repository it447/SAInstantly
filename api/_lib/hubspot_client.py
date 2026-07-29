import requests

API_BASE = "https://api.hubapi.com"


def _headers(api_key):
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def get_list(api_key, list_id):
    resp = requests.get(f"{API_BASE}/crm/v3/lists/{list_id}", headers=_headers(api_key), timeout=15)
    resp.raise_for_status()
    return resp.json()


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


def batch_read_contacts(api_key, contact_ids, properties=("email", "firstname", "lastname", "company")):
    if not contact_ids:
        return []
    resp = requests.post(
        f"{API_BASE}/crm/v3/objects/contacts/batch/read",
        headers=_headers(api_key),
        json={
            "properties": list(properties),
            "inputs": [{"id": cid} for cid in contact_ids],
        },
        timeout=20,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    contacts = []
    for r in results:
        props = r.get("properties", {})
        if not props.get("email"):
            continue
        contacts.append(
            {
                "email": props["email"].strip().lower(),
                "first_name": props.get("firstname") or "",
                "last_name": props.get("lastname") or "",
                "company": props.get("company") or "",
                "hubspot_id": r.get("id"),
            }
        )
    return contacts
