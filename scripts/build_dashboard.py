#!/usr/bin/env python3
"""
Rebuilds index.html's embedded CONTACTS array from fresh Folk data and
updates the LAST_UPDATED timestamp, in place.

Run from the repo root. Expects two inputs to already exist (an hourly
refresh routine must produce these itself via the Folk MCP connector before
calling this script -- this script does no Folk API calls of its own):

  data/people_condensed.json   -- list of {id, fullName, jobTitle, description,
                                   emails, urls, addresses, companies, cfv}
                                   for every priority contact (cfv = the
                                   Contacts-group customFieldValues object)
  data/notes_by_person.json    -- {person_id: [{content, createdAt}, ...]}
                                   from folk_search_notes for every contact
                                   (empty list where none exist)

Usage:  python3 scripts/build_dashboard.py

See HANDOFF.md and CLAUDE.md in this repo's source project
(Anomaly CRM/Priority Contacts Dashboard) for the full history and rationale
behind every rule encoded below. Do not re-introduce a `description` fallback
for `details` -- James explicitly asked (8 Oct 2026) for Folk's description
field to never be surfaced in the dashboard, because Folk's own enrichment
feature keeps silently repopulating it with LinkedIn-style bios that are not
relevant interactions.
"""
import json, re, sys, os, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

people = json.load(open(os.path.join(DATA, "people_condensed.json")))
notes_by_person = json.load(open(os.path.join(DATA, "notes_by_person.json")))

# Contacts whose Folk Status custom field = "Current Client" (per-person, not
# per-company). This list only needs updating if a contact's Status changes
# in Folk -- harmless to leave stale entries in (they just won't match an id
# that's no longer a priority contact), but if someone's current-client
# status changes, this list needs a manual edit.
CURRENT_IDS = {
    "per_2a2979ec-6e64-463c-abc7-49c2611d2455", "per_096e23be-cb2d-4a52-b036-e76470f737f1", "per_83c6d4ad-99bf-46a1-94f7-7859e29577dc",
    "per_c697b398-c406-4894-8c0e-a2d83fcff07a", "per_5b456dfe-3e5a-403c-b7e5-87299cbd4a1b", "per_08fcaad0-878e-49ab-98fe-eef125c98ddb",
    "per_0400eef7-ae76-4062-9c53-3dcc41c3aaaf", "per_04dc58a8-b2b4-4594-b2ce-d347867e9a15", "per_0b06108d-516f-4b97-b397-f3b88a0dee7a",
    "per_0f8b9712-7e57-427c-9daa-e1e0dab75b8b", "per_1fe066c5-128a-463a-9f8b-09e6904eb75d", "per_2221d38a-0729-4361-9f3b-bd71c88fb8a7",
    "per_27f1fce7-9635-4262-9d0a-6b71b0ab28ca", "per_29babc88-9a19-4610-b23b-26cde4f89bcd", "per_4d4c6822-0a4c-44cb-abf6-5e269ec072db",
    "per_534a0a05-eca9-495d-affe-02323279285b", "per_55b35db1-efe4-4d34-b3ff-dc1ba2c8d4f6", "per_56643941-70ab-4702-929f-13d1d670ef42",
    "per_855ded0c-8345-40a3-a77c-a742fa747813", "per_86892ee6-1e4e-4d7b-96f0-dae2c139090c", "per_8ff0be93-1e23-46fe-8d54-0ccdcee83511",
    "per_a30647f1-484a-4660-95b5-1aaa074de827", "per_c1e826b5-a636-426c-a4d4-bdb09d610f3b", "per_d5fbf561-20da-43ef-8966-f3c28ef863fe",
    "per_f58f5570-1148-4dac-a873-66a582b1cac3",
}

# "Prospect" status has no home in Folk (Type field has no Prospect option,
# and James chose 8 Oct 2026 to just clear the description that used to flag
# it rather than invent a workaround). Tracked here as dashboard-only state.
# If this stops matching reality, it needs a human to update it -- there is
# no way to re-derive it from Folk.
OVERRIDES = {
    "per_1383d4c1-422f-4b74-84a5-04aac8dd53aa": {"prospect": True},  # Jennifer Prakash
    "per_d319dc02-3dbc-4eff-9929-0169209815e7": {"prospect": True},  # Eileen Mannion
    "per_043e2ec2-2b6d-43cf-9bbb-bf83111a0b55": {"prospect": True},  # Alison Lomax
    "per_7bdc44b8-3308-40f3-8e4e-4192c6309c7e": {"prospect": True},  # Cilesta Van Doorn
}

# These contacts each carry exactly one Folk Note that is their static 30 Club
# committee-role bio, not a logged interaction -- same mislabelling problem
# as the description field, just via the notes feature instead. Route to
# `details`, not `interactions`. If more 30 Club members get added later,
# check their note content before assuming it's a real interaction and add
# their id here if so.
THIRTY_CLUB_BIO_IDS = {
    "per_5c60ebd9-a27e-4097-b994-3c04eb7f7636", "per_6d538583-aaa0-4d59-8475-3f8d38b85f2c", "per_dadb6a67-875f-4e1e-9e1f-7f4f3fb26719",
    "per_d9cfa635-11a8-4784-aac3-f9523638dd91", "per_9e814e44-ac20-411a-a266-92cf79db271d", "per_aa9e22f5-c248-41c9-a7f4-b650a9a68897",
    "per_07a283ba-50e6-42ba-a864-73a310fb1c21", "per_76fe2194-187b-40d6-8966-0464c5f6d015", "per_32f53632-ad26-452f-a767-fa7eeab3156b",
    "per_97c14ec0-54f6-49cf-ae9a-ff0169fef377", "per_3129485f-3bc2-4998-91c6-af54badcaa55", "per_d61522c3-0b67-4a9e-9205-fb1d77b7482b",
    "per_db025baa-49d5-4af3-acf8-77d6cd6cc643",
}

# Folk notes longer than this are self-authored bios / background dumps, not
# relevant logged interactions (James, 8 Oct 2026, re: Coral Cranmer's bio).
# Every legitimate short interaction note observed is well under this; the
# known bio-dumps are all 900-1900 chars.
LONG_NOTE_THRESHOLD = 350

def folk_note_to_interaction(note):
    content = note.get("content", "").strip()
    created = note.get("createdAt", "")
    m = re.match(r"^\s*(\d{1,2}-\d{1,2}-\d{2,4})\s*\|\s*([A-Za-z]+)\s*\|\s*(.*)$", content)
    if m:
        date, itype, rest = m.groups()
        hashtag = None
        hm = re.search(r"(#\w+)\s*$", rest)
        if hm:
            hashtag = hm.group(1)
            rest = rest[: hm.start()].strip()
        return {"date": date, "type": itype.upper(), "text": rest.strip(), "hashtag": hashtag}
    date = created[8:10] + "-" + created[5:7] + "-" + created[2:4] if len(created) >= 10 else "??"
    return {"date": date, "type": "NOTE", "text": content, "hashtag": None}

def esc_js_str(s):
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

skipped_long_notes = []
contacts = []
for p in people:
    cfv = p.get("cfv", {})
    companies = p.get("companies") or []
    emails = p.get("emails") or []
    urls = p.get("urls") or []
    addresses = p.get("addresses") or []
    linkedin = next((u for u in urls if "linkedin.com" in u.lower()), (urls[0] if urls else ""))

    ea_name = cfv.get("EA Name") or ""
    ea_email = cfv.get("EA email") or ""

    override = OVERRIDES.get(p["id"], {})
    real_notes = notes_by_person.get(p["id"], [])

    if p["id"] in THIRTY_CLUB_BIO_IDS:
        bio = (real_notes[0]["content"] if real_notes else "").replace("**", "").strip()
        details = bio
    else:
        # NEVER fall back to live `description` here -- see module docstring.
        details = override.get("details", "")

    interactions = list(override.get("interactions", []))
    if p["id"] not in THIRTY_CLUB_BIO_IDS:
        for n in real_notes:
            content = n.get("content", "")
            if len(content) > LONG_NOTE_THRESHOLD:
                skipped_long_notes.append((p["fullName"], len(content)))
                continue
            interactions.append(folk_note_to_interaction(n))

    contacts.append({
        "id": p["id"],
        "name": p["fullName"],
        "title": p.get("jobTitle") or "\u2014",
        "company": companies[0] if companies else "",
        "email": emails[0] if emails else "",
        "linkedin": linkedin,
        "location": addresses[0] if addresses else "",
        "type": cfv.get("Type") or "Client",
        "cannes": bool(cfv.get("Cannes Attendance")),
        "newsletter": bool(cfv.get("Newsletter sends")),
        "owner": cfv.get("R-ship Owner") or "",
        "ea": {"name": ea_name, "email": ea_email},
        "prospect": bool(override.get("prospect", False)),
        "flags": override.get("flags", []),
        "details": details,
        "interactions": interactions,
    })

contacts.sort(key=lambda c: c["name"])

def js_obj(c):
    ea = '{name:"%s",email:"%s"}' % (esc_js_str(c["ea"]["name"]), esc_js_str(c["ea"]["email"]))
    flags = "[" + ",".join('"%s"' % esc_js_str(f) for f in c["flags"]) + "]"
    ix = []
    for i in c["interactions"]:
        hashtag = i.get("hashtag")
        hpart = ('"%s"' % esc_js_str(hashtag)) if hashtag else "null"
        ix.append('{date:"%s",type:"%s",text:"%s",hashtag:%s}' % (
            esc_js_str(i.get("date", "??")), esc_js_str(i.get("type", "NOTE")), esc_js_str(i.get("text", "")), hpart
        ))
    interactions_js = "[" + ",".join(ix) + "]"
    return ('{id:"%s",name:"%s",title:"%s",company:"%s",email:"%s",linkedin:"%s",location:"%s",type:"%s",'
            'cannes:%s,newsletter:%s,owner:"%s",ea:%s,prospect:%s,flags:%s,details:"%s",interactions:%s}') % (
        c["id"], esc_js_str(c["name"]), esc_js_str(c["title"]), esc_js_str(c["company"]), esc_js_str(c["email"]),
        esc_js_str(c["linkedin"]), esc_js_str(c["location"]), c["type"],
        "true" if c["cannes"] else "false", "true" if c["newsletter"] else "false",
        esc_js_str(c["owner"]), ea, "true" if c["prospect"] else "false", flags,
        esc_js_str(c["details"]), interactions_js
    )

js_array = "const CONTACTS = [\n" + ",\n".join(js_obj(c) for c in contacts) + "\n];"

# String-aware balance check before touching the real file.
in_str = False
esc = False
depth_curly = depth_sq = 0
for ch in js_array:
    if in_str:
        if esc:
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == '"':
            in_str = False
        continue
    if ch == '"':
        in_str = True
    elif ch == "{":
        depth_curly += 1
    elif ch == "}":
        depth_curly -= 1
    elif ch == "[":
        depth_sq += 1
    elif ch == "]":
        depth_sq -= 1
if depth_curly != 0 or depth_sq != 0 or in_str:
    print(f"ABORT: generated CONTACTS array failed its own balance check "
          f"(curly={depth_curly} sq={depth_sq} in_str={in_str}) -- not touching index.html", file=sys.stderr)
    sys.exit(1)

index_path = os.path.join(ROOT, "index.html")
html = open(index_path, encoding="utf-8").read()

pattern = re.compile(r"const CONTACTS = \[.*?\n\];", re.DOTALL)
m = pattern.search(html)
if not m:
    print("ABORT: could not find 'const CONTACTS = [...]' in index.html -- not touching it", file=sys.stderr)
    sys.exit(1)
html = html[: m.start()] + js_array + html[m.end() :]

now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
html, n = re.subn(r'const LAST_UPDATED = "[^"]*";', f'const LAST_UPDATED = "{now}";', html)
if n != 1:
    print("ABORT: could not find exactly one LAST_UPDATED line in index.html -- not touching it", file=sys.stderr)
    sys.exit(1)

with open(index_path, "w", encoding="utf-8") as f:
    f.write(html)

print(f"OK: wrote {len(contacts)} contacts to index.html, LAST_UPDATED={now}")
if skipped_long_notes:
    print(f"Skipped {len(skipped_long_notes)} over-{LONG_NOTE_THRESHOLD}-char notes (self-authored bios, not interactions):")
    for name, length in skipped_long_notes:
        print(f"  {name} ({length} chars)")
