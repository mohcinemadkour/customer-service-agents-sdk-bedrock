import asyncio, json, os, sys
from playwright.async_api import async_playwright

LF = "https://langfuse.3.143.190.229.sslip.io"
P = LF + "/project/evaltrust-lab"
V11 = "cmw6mwoik0018qq2al7mf4n69"
TRACE = P + "/traces/83950e849c2214c26b5e69360a860141"
OUT = "/opt/capture/shots"
GH = "https://github.com/mohcinemadkour/customer-service-agents-sdk-bedrock/blob/main/evaluation/"
creds = dict(l.strip().split("=", 1) for l in open("/opt/capture/.creds") if "=" in l)

FIND = """(args) => {
  const [needle, which] = args; const out = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) {
    const n = w.currentNode; const i = n.textContent.indexOf(needle);
    if (i < 0) continue;
    const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + needle.length);
    for (const b of r.getClientRects()) {
      if (b.width > 2 && b.height > 2 && b.bottom > 0 && b.top < innerHeight && b.right > 0 && b.left < innerWidth)
        out.push([Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)]);
    }
  }
  return which === 'all' ? out : (which === 'last' ? out[out.length-1] : out[0]) || null;
}"""

REVEAL = """(needle) => {
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) { const n = w.currentNode;
    if (n.textContent.includes(needle)) { n.parentElement.scrollIntoView({block:'center', inline:'center'}); return true; } }
  return false;
}"""

async def find(page, text, which="first"):
    return await page.evaluate(FIND, [text, which])

async def rows50(page):
    lab = page.get_by_text("Rows per page").first
    await lab.scroll_into_view_if_needed()
    await lab.locator("xpath=following::button[1]").click(); await page.wait_for_timeout(700)
    await page.get_by_role("option", name="50", exact=True).click(); await page.wait_for_timeout(4000)

async def goto_page(page, n):
    inp = page.locator("input[type=number]").last
    await inp.fill(str(n)); await inp.press("Enter"); await page.wait_for_timeout(4000)

async def run_actions(page, actions):
    for a in actions:
        try:
            k = a[0]
            if k == "click_text":
                await page.get_by_text(a[1], exact=True).first.click(); await page.wait_for_timeout(3500)
            elif k == "click_row":
                await page.get_by_text(a[1]).first.click(); await page.wait_for_timeout(3000)
            elif k == "rows50":
                await rows50(page); await page.evaluate("window.scrollTo(0,0)")
            elif k == "page":
                await goto_page(page, a[1])
            elif k == "reveal":
                print("  reveal", a[1], await page.evaluate(REVEAL, a[1])); await page.wait_for_timeout(1500)
            elif k == "wait":
                await page.wait_for_timeout(a[1])
        except Exception as e:
            print("  action warn", a, type(e).__name__, str(e)[:100])

SHOTS = [
    ("home", P, [], {"project": ("EvalTrust Lab", "first")}),
    ("traces", P + "/traces", [], {"session": ("eval-AL-0", "first"), "tracing": ("Tracing", "first")}),
    ("trace", TRACE, [], {"g1": ("Relevance Guardrail", "first"), "g2": ("Jailbreak Guardrail", "first"),
                           "tool": ("get_trip_details", "first"), "seat": ("handoff to Seat", "first")}),
    ("trace_tool", TRACE, [("click_text", "get_trip_details")], {"tool": ("get_trip_details", "first"), "hit": ("LL0EZ6", "first"), "on_time": ("on_time", "first")}),
    ("leak", TRACE, [("click_row", "1,644 tokens"), ("reveal", "Current Seat")], {"conf": ("LL0EZ6", "last"), "seat": ("23A", "last"), "flight": ("FLT-123", "last")}),
    ("sessions", P + "/sessions", [], {"session": ("eval-AL-0", "first")}),
    ("datasets", P + "/datasets", [], {"v11": ("golden-airline_v1.1", "first"), "tau": ("tau2-airline", "first")}),
    ("items", P + f"/datasets/{V11}/items", [], {"al51": ("AL-051", "first"), "exp": ("Expected Output", "first")}),
    ("experiments", P + f"/datasets/{V11}/items", [("click_text", "Experiments")], {"run": ("airline-v1.1-run1", "first")}),
    ("experiments_gate", P + f"/datasets/{V11}/items", [("click_text", "Experiments"), ("reveal", "evaltrust.gate")], {"gate": ("evaltrust.gate", "all"), "crit": ("criterion.booking_isolation", "first")}),
    ("run", P + "/experiments/results?baseline=9000f818a238fd5b", [("rows50",)], {"summary": ("SUMMARY", "first"), "bi": ("booking_isolation", "first"), "rows": ("Rows per page", "first")}),
    ("zero", P + "/experiments/results?baseline=9000f818a238fd5b", [("rows50",), ("page", 2), ("reveal", "AL-051")], {"al51": ("AL-051", "first"), "bi": ("booking_isolation", "all")}),
    ("compare", P + "/experiments/results?baseline=84a0cef6d1c9b9ac&c=406b14d157f189d0", [], {"base": ("Baseline", "first"), "cmp": ("Compare", "first"), "routing": ("routing_correct", "first")}),
    ("tau", P + "/experiments/results?baseline=638f595efca62aa0", [], {"summary": ("SUMMARY", "first")}),
    ("gh_criteria", GH + "criteria_airline_v1.1.json", [("reveal", "booking_isolation")], {"bi": ("booking_isolation", "last"), "owner": ("Privacy / Compliance", "first")}),
    ("gh_scorer", GH + "scorers_airline.py#L112", [("wait", 2500), ("reveal", "def booking_isolation")], {"def": ("def booking_isolation", "first"), "fid": ("foreign_ids", "first")}),
    ("report", "http://127.0.0.1:8080/report/airline-v1.1-run1", [], {"hold": ("HOLD", "first"), "bi": ("another customer", "first")}),
]

async def main(only=None):
    os.makedirs(OUT, exist_ok=True)
    boxes = {}
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=2, color_scheme="light")
        page = await ctx.new_page()
        await page.goto(LF + "/auth/sign-in", wait_until="networkidle")
        await page.fill("input[name=email]", creds["LF_EMAIL"])
        await page.fill("input[type=password]", creds["LF_PASSWORD"])
        await page.click("button[type=submit]")
        await page.wait_for_timeout(4000)
        for name, url, actions, texts in SHOTS:
            if only and name not in only:
                continue
            try:
                await page.goto(url, wait_until="networkidle", timeout=45000)
            except Exception as e:
                print(name, "goto warn", type(e).__name__)
            await page.wait_for_timeout(3500)
            await run_actions(page, actions)
            await page.screenshot(path=f"{OUT}/{name}.png")
            boxes[name] = {k: await find(page, t, w) for k, (t, w) in texts.items()}
            print(name, {k: v for k, v in boxes[name].items()}, flush=True)
        await b.close()
    json.dump(boxes, open(f"{OUT}/boxes.json", "w"), indent=1)

asyncio.run(main(set(sys.argv[1:]) or None))
