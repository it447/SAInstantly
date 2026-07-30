import os

import requests

# The legacy v1 contacts API, not the newer v3 CRM Lists API - v3 Lists can
# require scopes/plan tiers that aren't available on every HubSpot account,
# while v1 has been broadly available for years and is what's proven to work.
API_BASE = "https://api.hubapi.com"


def get_api_key():
    """The HubSpot private-app API key, configured only via the
    HUBSPOT_API_KEY environment variable (Vercel project settings) -- never
    entered through the UI or stored in Redis."""
    return os.environ.get("HUBSPOT_API_KEY", "").strip()


def _headers(api_key):
    return {"Authorization": f"Bearer {api_key}"}


def get_list(api_key, list_id):
    resp = requests.get(f"{API_BASE}/contacts/v1/lists/{list_id}", headers=_headers(api_key), timeout=15)
    resp.raise_for_status()
    return resp.json()


def list_all_lists(api_key, count=250):
    """Returns every contact list as [{id, name}], for populating the list picker."""
    all_lists = []
    offset = None
    while True:
        params = {"count": count}
        if offset is not None:
            params["offset"] = offset
        resp = requests.get(
            f"{API_BASE}/contacts/v1/lists",
            headers=_headers(api_key),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        for hs_list in data.get("lists", []):
            list_id = hs_list.get("listId")
            all_lists.append({"id": str(list_id), "name": hs_list.get("name") or f"List {list_id}"})
        if not data.get("has-more"):
            break
        offset = data.get("offset")
    all_lists.sort(key=lambda l: l["name"].lower())
    return all_lists


DEFAULT_PROPERTIES = ("email", "firstname", "lastname", "company")


def get_list_contacts(api_key, list_id, properties=DEFAULT_PROPERTIES, count=100):
    """Paginates the list's contacts and returns each as
    {email, hubspot_id, properties: {name: value}}, using HubSpot's own
    internal property names (e.g. "firstname", not "first_name") so merge
    tags typed as {{firstname}} line up exactly with what a user picks from
    the contact-properties list.
    """
    properties = list(dict.fromkeys(list(properties) + ["email"]))  # email is always required, no dupes
    contacts = []
    vid_offset = None
    while True:
        params = [("count", count)] + [("property", p) for p in properties]
        if vid_offset:
            params.append(("vidOffset", vid_offset))
        resp = requests.get(
            f"{API_BASE}/contacts/v1/lists/{list_id}/contacts/all",
            headers=_headers(api_key),
            params=params,
            timeout=20,
        )
        resp.raise_for_status()
        body = resp.json()
        for c in body.get("contacts", []):
            props = c.get("properties", {})
            email = (props.get("email") or {}).get("value", "").strip().lower()
            if not email:
                continue
            contacts.append(
                {
                    "email": email,
                    "hubspot_id": str(c.get("vid")),
                    "properties": {name: (props.get(name) or {}).get("value", "") for name in properties},
                }
            )
        if not body.get("has-more"):
            break
        vid_offset = body.get("vid-offset")
    return contacts


def get_contact_properties(api_key):
    """Returns the list of contact properties available for personalization:
    [{name, label, group_name}], skipping hidden/calculated/read-only ones
    that wouldn't make sense as merge tags. This is the standard v3
    properties-schema endpoint (unrelated to the Lists API), broadly
    available regardless of plan tier.
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
