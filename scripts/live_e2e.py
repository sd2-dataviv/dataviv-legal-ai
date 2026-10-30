"""End-to-end live check: two collaborators over the real WebSocket, a real AI suggestion, approval lock."""

import asyncio
import json
import sys
import time

import httpx
import websockets
from pycrdt import Decoder, Doc, Text, create_sync_message, create_update_message, handle_sync_message

BASE = __import__("os").environ.get("DATAVIV_BASE", "127.0.0.1:8010")
DRAFT = sys.argv[1]
T0 = time.monotonic()


def say(msg: str) -> None:
    print(f"[{time.monotonic() - T0:5.1f}s] {msg}", flush=True)


class Person:
    def __init__(self, name: str, token: str) -> None:
        self.name, self.token = name, token
        self.doc = Doc()
        self.text = self.doc.get("content", type=Text)
        self.events: list[dict] = []
        self.closed: int | None = None

    async def connect(self) -> None:
        self.ws = await websockets.connect(f"ws://{BASE}/v1/drafts/{DRAFT}/live", subprotocols=["dataviv", self.token])
        await self.ws.send(create_sync_message(self.doc))
        await self.pump(1.0)

    async def pump(self, seconds: float = 0.6) -> None:
        end = time.monotonic() + seconds
        while self.closed is None and (left := end - time.monotonic()) > 0:
            try:
                data = await asyncio.wait_for(self.ws.recv(), timeout=left)
            except TimeoutError:
                return
            except websockets.ConnectionClosed as e:
                self.closed = e.rcvd.code if e.rcvd else -1
                return
            if data[0] == 0:
                reply = handle_sync_message(data[1:], self.doc)
                if reply:
                    await self.ws.send(reply)
            elif data[0] == 100:
                self.events.append(json.loads(Decoder(data[1:]).read_var_string()))

    async def type_at(self, at: int, s: str) -> None:
        before = self.doc.get_state()
        self.text.insert(len(str(self.text)[:at].encode()), s)
        try:
            await self.ws.send(create_update_message(self.doc.get_update(before)))
        except websockets.ConnectionClosed:
            pass

    def value(self) -> str:
        return str(self.text)


async def main() -> None:
    http = httpx.Client(base_url=f"http://{BASE}", timeout=180)
    ids = {i["name"]: i for i in http.get("/v1/dev/identities").json()}
    rohan = Person("Rohan", ids["Rohan Gupta"]["access_token"])
    asha = Person("Asha", ids["Asha Sharma"]["access_token"])
    hr = {"Authorization": f"Bearer {rohan.token}"}
    ha = {"Authorization": f"Bearer {asha.token}"}

    say("1/6 both connect")
    await rohan.connect()
    await asha.connect()
    assert rohan.value() == asha.value()
    say("    same text on both sides; Rohan types at the top while Asha edits her P.S. at the same moment")
    await asyncio.gather(
        rohan.type_at(0, "WITHOUT PREJUDICE\n\n"),
        asha.type_at(asha.value().index("15-day"), "clear "),
    )
    for _ in range(3):
        await asyncio.gather(rohan.pump(), asha.pump())
    assert rohan.value() == asha.value(), "did not converge"
    assert rohan.value().startswith("WITHOUT PREJUDICE") and "a clear 15-day cure period" in rohan.value()
    say("    converged: both see WITHOUT PREJUDICE at the top and 'a clear 15-day cure period' in the P.S.")

    say("2/6 Rohan asks the AI (Groq) to rewrite clause 3, grounded in the matter's documents")
    clause = "3. Our client may end the agreement if this is not fixed."
    text = rohan.value()
    t = time.monotonic()
    r = http.post(
        f"/v1/drafts/{DRAFT}/suggestions",
        json={
            "instruction": "Rewrite as a formal termination warning: the Seller must cure the default within 15 days "
            "of this notice, failing which our client may terminate under the termination clause of the SPA. Cite it.",
            "selection": {"text": clause, "start": text.index(clause)},
            "use_documents": True,
        },
        headers=hr,
    )
    assert r.status_code == 201, r.text
    s = r.json()
    say(f"    suggestion in {time.monotonic() - t:.1f}s from {s['model']}, ${s['cost_usd']:.5f}")
    say(f"    REPLACEMENT: {s['replacement']}")
    say(f"    WHY: {s['explanation']}")
    say(f"    SOURCES: {[(x['document_title'], x.get('section_path')) for x in s['sources']]}")
    say(f"    WARNINGS: {s['warnings']}")
    await asyncio.gather(rohan.pump(), asha.pump())
    assert any(e.get("what") == "suggestion" for e in asha.events), "Asha was not told"
    assert clause in asha.value(), "the draft must not change before someone accepts"
    say("    Asha was notified live; the draft is unchanged until someone accepts")

    say("3/6 Asha accepts the suggestion")
    r = http.post(f"/v1/drafts/{DRAFT}/suggestions/{s['id']}/accept", headers=ha)
    assert r.status_code == 200 and r.json()["status"] == "accepted", r.text
    await asyncio.gather(rohan.pump(), asha.pump())
    assert rohan.value() == asha.value() and s["replacement"] in rohan.value() and clause not in rohan.value()
    say("    applied on both screens")

    say("4/6 Rohan saves the working copy as a version")
    r = http.post(f"/v1/drafts/{DRAFT}/live/save", json={"note": "Joint edit + AI clause 3"}, headers=hr)
    assert r.status_code == 201, r.text
    v = r.json()
    say(f"    v{v['version_no']} saved by {v['created_by']['name']}, contributors: {[c['name'] for c in v['contributors']]}")

    say("5/6 Rohan sends it to Asha (matter lead) for approval: editing locks for everyone")
    r = http.post(f"/v1/drafts/{DRAFT}/review", json={"reviewer_id": ids["Asha Sharma"]["user_id"], "message": "Final notice for your approval"}, headers=hr)
    assert r.status_code == 200, r.text
    await asyncio.gather(rohan.pump(), asha.pump())
    assert {"type": "status", "status": "in_review", "locked": True} in rohan.events
    await rohan.type_at(0, "x")
    await rohan.pump(1.0)
    say(f"    a keystroke while locked -> server closed Rohan's socket with {rohan.closed} (4423 = locked for approval)")
    assert rohan.closed == 4423

    say("6/6 Asha approves")
    r = http.post(f"/v1/drafts/{DRAFT}/review/decision", json={"decision": "approve", "note": "Approved, send it."}, headers=ha)
    assert r.status_code == 200, r.text
    d = r.json()
    await asha.pump()
    assert {"type": "status", "status": "approved", "locked": False} in asha.events
    say(f"    status {d['status']}, approved version v{d['approved_version_no']} by {d['approved_by']['name']}; editing unlocked")
    final = http.get(f"/v1/drafts/{DRAFT}/versions/{d['approved_version_no']}", headers=ha).json()["content"]
    print("\n===== APPROVED TEXT =====\n" + final + "\n=========================", flush=True)
    await asha.ws.close()


asyncio.run(main())
